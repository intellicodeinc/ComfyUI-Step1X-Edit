# ComfyUI node for step1x-edit
# Original Project Repository https://github.com/stepfun-ai/Step1X-Edit/
import os
import argparse
import datetime
import json 
import itertools
import math
import time
from pathlib import Path
from typing import Literal

import numpy as np
import torch
from einops import rearrange, repeat
from PIL import Image, ImageOps
from safetensors.torch import load_file
from torchvision.transforms import functional as F
from torchvision.transforms import ToTensor
from tqdm import tqdm 

from . import sampling
from .modules.autoencoder import AutoEncoder
from .modules.conditioner import Qwen25VL_7b_Embedder as Qwen2VLEmbedder
from .modules.model_edit import Step1XParams, Step1XEdit

import folder_paths

# Derived from Step1X official inference code

def cudagc():
    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()

def load_state_dict(model, ckpt_path, device="cuda", strict=False, assign=True):
    if Path(ckpt_path).suffix == ".safetensors":
        state_dict = load_file(os.path.join(folder_paths.models_dir, 'Step1x-Edit', ckpt_path), device)
    else:
        state_dict = torch.load(ckpt_path, map_location="cpu")

    missing, unexpected = model.load_state_dict(
        state_dict, strict=strict, assign=assign
    )
    if len(missing) > 0 and len(unexpected) > 0:
        print(f"Got {len(missing)} missing keys:\n\t" + "\n\t".join(missing))
        print("\n" + "-" * 79 + "\n")
        print(f"Got {len(unexpected)} unexpected keys:\n\t" + "\n\t".join(unexpected))
    elif len(missing) > 0:
        print(f"Got {len(missing)} missing keys:\n\t" + "\n\t".join(missing))
    elif len(unexpected) > 0:
        print(f"Got {len(unexpected)} unexpected keys:\n\t" + "\n\t".join(unexpected))
    return model


def load_models(
    dit_path=None,
    ae_path=None,
    qwen2vl_model_path=None,
    device="cuda",
    max_length=256,
    dtype=torch.bfloat16,
    
):
    qwen2vl_encoder = Qwen2VLEmbedder(
        qwen2vl_model_path,
        device=device,
        max_length=max_length,
        dtype=dtype,
    )

    with torch.device("meta"):
        ae = AutoEncoder(
            resolution=256,
            in_channels=3,
            ch=128,
            out_ch=3,
            ch_mult=[1, 2, 4, 4],
            num_res_blocks=2,
            z_channels=16,
            scale_factor=0.3611,
            shift_factor=0.1159,
        )

        step1x_params = Step1XParams(
            in_channels=64,
            out_channels=64,
            vec_in_dim=768,
            context_in_dim=4096,
            hidden_size=3072,
            mlp_ratio=4.0,
            num_heads=24,
            depth=19,
            depth_single_blocks=38,
            axes_dim=[16, 56, 56],
            theta=10_000,
            qkv_bias=True,
        )
        dit = Step1XEdit(step1x_params)

    ae = load_state_dict(ae, ae_path, 'cpu')
    dit = load_state_dict(
        dit, dit_path, 'cpu'
    )

    ae = ae.to(dtype=torch.float32)

    return ae, dit, qwen2vl_encoder
class ImageGenerator:
    def __init__(
        self,
        dit_path=None,
        ae_path=None,
        qwen2vl_model_path=None,
        device="cuda",
        max_length=640,
        dtype=torch.bfloat16,
        offload=False,
        quantized=False,
    ) -> None:
        self.device = torch.device(device)
        self.ae, self.dit, self.llm_encoder = load_models(
            dit_path=dit_path,
            ae_path=ae_path,
            qwen2vl_model_path=qwen2vl_model_path,
            max_length=max_length,
            dtype=dtype,
            device="cpu" if offload else device
        )
        if not quantized:
            self.dit = self.dit.to(dtype=torch.bfloat16)
        if not offload:
            self.dit = self.dit.to(device=self.device)
            self.ae = self.ae.to(device=self.device)
            self.llm_encoder = self.llm_encoder.to(device=self.device)
        self.quantized = quantized 
        self.offload = offload
        
    def prepare(self, prompt, img, ref_image, ref_image_raw):
        bs, _, h, w = img.shape
        bs, _, ref_h, ref_w = ref_image.shape

        assert h == ref_h and w == ref_w

        if bs == 1 and not isinstance(prompt, str):
            bs = len(prompt)
        elif bs >= 1 and isinstance(prompt, str):
            prompt = [prompt] * bs

        img = rearrange(img, "b c (h ph) (w pw) -> b (h w) (c ph pw)", ph=2, pw=2)
        ref_img = rearrange(ref_image, "b c (ref_h ph) (ref_w pw) -> b (ref_h ref_w) (c ph pw)", ph=2, pw=2)
        if img.shape[0] == 1 and bs > 1:
            img = repeat(img, "1 ... -> bs ...", bs=bs)
            ref_img = repeat(ref_img, "1 ... -> bs ...", bs=bs)

        img_ids = torch.zeros(h // 2, w // 2, 3)

        img_ids[..., 1] = img_ids[..., 1] + torch.arange(h // 2)[:, None]
        img_ids[..., 2] = img_ids[..., 2] + torch.arange(w // 2)[None, :]
        img_ids = repeat(img_ids, "h w c -> b (h w) c", b=bs)

        ref_img_ids = torch.zeros(ref_h // 2, ref_w // 2, 3)

        ref_img_ids[..., 1] = ref_img_ids[..., 1] + torch.arange(ref_h // 2)[:, None]
        ref_img_ids[..., 2] = ref_img_ids[..., 2] + torch.arange(ref_w // 2)[None, :]
        ref_img_ids = repeat(ref_img_ids, "ref_h ref_w c -> b (ref_h ref_w) c", b=bs)

        if isinstance(prompt, str):
            prompt = [prompt]
        if self.offload:
            self.llm_encoder = self.llm_encoder.to(self.device)
        txt, mask = self.llm_encoder(prompt, ref_image_raw)
        if self.offload:
            self.llm_encoder = self.llm_encoder.cpu()
            cudagc()

        txt_ids = torch.zeros(bs, txt.shape[1], 3)

        img = torch.cat([img, ref_img.to(device=img.device, dtype=img.dtype)], dim=-2)
        img_ids = torch.cat([img_ids, ref_img_ids], dim=-2)


        return {
            "img": img,
            "mask": mask,
            "img_ids": img_ids.to(img.device),
            "llm_embedding": txt.to(img.device),
            "txt_ids": txt_ids.to(img.device),
        }

    @staticmethod
    def process_diff_norm(diff_norm, k):
        pow_result = torch.pow(diff_norm, k)

        result = torch.where(
            diff_norm > 1.0,
            pow_result,
            torch.where(diff_norm < 1.0, torch.ones_like(diff_norm), diff_norm),
        )
        return result

    def denoise(
        self,
        img: torch.Tensor,
        img_ids: torch.Tensor,
        llm_embedding: torch.Tensor,
        txt_ids: torch.Tensor,
        timesteps: list[float],
        cfg_guidance: float = 4.5,
        mask=None,
        show_progress=False,
        timesteps_truncate=1.0,
    ):
        if self.offload:
            self.dit = self.dit.to(self.device)
        if show_progress:
            pbar = tqdm(itertools.pairwise(timesteps), desc='denoising...')
        else:
            pbar = itertools.pairwise(timesteps)
        for t_curr, t_prev in pbar:
            if img.shape[0] == 1 and cfg_guidance != -1:
                img = torch.cat([img, img], dim=0)
            t_vec = torch.full(
                (img.shape[0],), t_curr, dtype=img.dtype, device=img.device
            )

            txt, vec = self.dit.connector(llm_embedding, t_vec, mask)


            pred = self.dit(
                img=img,
                img_ids=img_ids,
                txt=txt,
                txt_ids=txt_ids,
                y=vec,
                timesteps=t_vec,
            )

            if cfg_guidance != -1:
                cond, uncond = (
                    pred[0 : pred.shape[0] // 2, :],
                    pred[pred.shape[0] // 2 :, :],
                )
                if t_curr > timesteps_truncate:
                    diff = cond - uncond
                    diff_norm = torch.norm(diff, dim=(2), keepdim=True)
                    pred = uncond + cfg_guidance * (
                        cond - uncond
                    ) / self.process_diff_norm(diff_norm, k=0.4)
                else:
                    pred = uncond + cfg_guidance * (cond - uncond)
            tem_img = img[0 : img.shape[0] // 2, :] + (t_prev - t_curr) * pred
            img_input_length = img.shape[1] // 2
            img = torch.cat(
                [
                tem_img[:, :img_input_length],
                img[ : img.shape[0] // 2, img_input_length:],
                ], dim=1
            )

        if self.offload:
            self.dit = self.dit.cpu()
            cudagc()

        return img[:, :img.shape[1] // 2]

    @staticmethod
    def unpack(x: torch.Tensor, height: int, width: int) -> torch.Tensor:
        return rearrange(
            x,
            "b (h w) (c ph pw) -> b c (h ph) (w pw)",
            h=math.ceil(height / 16),
            w=math.ceil(width / 16),
            ph=2,
            pw=2,
        )
    
    @staticmethod
    def tensor2pil(image):
        return Image.fromarray(np.clip(255. * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
    
    # PIL to Tensor
    @staticmethod
    def pil2tensor(image):
        return torch.from_numpy(np.array(image).astype(np.float32) / 255.0).unsqueeze(0)

    @staticmethod
    def load_image(image):
        from PIL import Image

        if isinstance(image, np.ndarray):
            image = torch.from_numpy(image).permute(2, 0, 1).float() / 255.0
            image = image.unsqueeze(0)
            return image
        elif isinstance(image, Image.Image):
            image = F.to_tensor(image.convert("RGB"))
            image = image.unsqueeze(0)
            return image
        elif isinstance(image, torch.Tensor):
            return image
        elif isinstance(image, str):
            image = F.to_tensor(Image.open(image).convert("RGB"))
            image = image.unsqueeze(0)
            return image
        else:
            raise ValueError(f"Unsupported image type: {type(image)}")

    def output_process_image(self, resize_img, image_size):
        res_image = resize_img.resize(image_size)
        return res_image
    
    def input_process_image(self, img, img_size=512):
        # 1. 打开图片
        img = self.tensor2pil(img)
        w, h = img.size
        r = w / h 

        if w > h:
            w_new = math.ceil(math.sqrt(img_size * img_size * r))
            h_new = math.ceil(w_new / r)
        else:
            h_new = math.ceil(math.sqrt(img_size * img_size / r))
            w_new = math.ceil(h_new * r)
        h_new = math.ceil(h_new) // 16 * 16
        w_new = math.ceil(w_new) // 16 * 16

        img_resized = img.resize((w_new, h_new))
        return img_resized, img.size

    @torch.inference_mode()
    def generate_image(
        self,
        prompt,
        negative_prompt,
        ref_images,
        num_steps,
        cfg_guidance,
        seed,
        num_samples=1,
        init_image=None,
        image2image_strength=0.0,
        show_progress=False,
        size_level=512,
    ):
        assert num_samples == 1, "num_samples > 1 is not supported yet."
        ref_images_raw, img_info = self.input_process_image(ref_images, img_size=size_level)
        
        width, height = ref_images_raw.width, ref_images_raw.height


        ref_images_raw = self.load_image(ref_images_raw)
        ref_images_raw = ref_images_raw.to(self.device)
        if self.offload:
            try:
                self.ae = self.ae.to(self.device)
            except NotImplementedError:
                print("ae is not moved to device")
                self.ae = self.ae.to_empty(device=self.device)
                self.ae = self.ae.to(self.device)
            
        ref_images = self.ae.encode(ref_images_raw.to(self.device) * 2 - 1)
        if self.offload:
            self.ae = self.ae.cpu()
            cudagc()

        seed = int(seed)
        seed = torch.Generator(device="cpu").seed() if seed < 0 else seed

        t0 = time.perf_counter()

        if init_image is not None:
            init_image = self.load_image(init_image)
            init_image = init_image.to(self.device)
            init_image = torch.nn.functional.interpolate(init_image, (height, width))
            if self.offload:
                self.ae = self.ae.to(self.device)
            init_image = self.ae.encode(init_image.to() * 2 - 1)
            if self.offload:
                self.ae = self.ae.cpu()
                cudagc()
        
        x = torch.randn(
            num_samples,
            16,
            height // 8,
            width // 8,
            device=self.device,
            dtype=torch.bfloat16,
            generator=torch.Generator(device=self.device).manual_seed(seed),
        )

        timesteps = sampling.get_schedule(
            num_steps, x.shape[-1] * x.shape[-2] // 4, shift=True
        )

        if init_image is not None:
            t_idx = int((1 - image2image_strength) * num_steps)
            t = timesteps[t_idx]
            timesteps = timesteps[t_idx:]
            x = t * x + (1.0 - t) * init_image.to(x.dtype)

        x = torch.cat([x, x], dim=0)
        ref_images = torch.cat([ref_images, ref_images], dim=0)
        ref_images_raw = torch.cat([ref_images_raw, ref_images_raw], dim=0)
        inputs = self.prepare([prompt, negative_prompt], x, ref_image=ref_images, ref_image_raw=ref_images_raw)
        with torch.autocast(device_type=self.device.type, dtype=torch.bfloat16):
            x = self.denoise(
                **inputs,
                cfg_guidance=cfg_guidance,
                timesteps=timesteps,
                show_progress=show_progress,
                timesteps_truncate=1.0,
            )
            x = self.unpack(x.float(), height, width)
            if self.offload:
                self.ae = self.ae.to(self.device)
            x = self.ae.decode(x)
            if self.offload:
                self.ae = self.ae.cpu()
                cudagc()
            x = x.clamp(-1, 1)
            x = x.mul(0.5).add(0.5)

        t1 = time.perf_counter()
        print(f"Done in {t1 - t0:.1f}s.")
        for img in x.float():
            image = self.output_process_image(F.to_pil_image(img), img_info)
            img = self.pil2tensor(image)
            break
        return img

class ConditionedImageGenerator(ImageGenerator):
    
    def as_input_process_image(self, img, ref_size, criteria : Literal["width", "height"]= "height", fit_to_ref : bool = True):
        img = self.tensor2pil(img)
        w, h = img.size
        r = w / h 

        LEFT, TOP, RIGHT, BOTTOM = 0, 1, 2, 3

        ref_w, ref_h = ref_size
        crop = [0, 0, 0, 0]
        if criteria == "height":
            h_new = ref_h
            r = ref_h / h
            w_new = math.ceil(w * r)
            crop[LEFT] = (w_new - ref_w) // 2     
            crop[RIGHT] = crop[LEFT] + ref_w
        else:
            w_new = ref_w
            r = ref_w / w
            h_new = math.ceil(h * r)
            crop[TOP] = (h_new - ref_h) // 2
            crop[BOTTOM] = crop[TOP] + ref_h

        img_resized = img.resize((w_new, h_new))

        if fit_to_ref:
            img_resized = img_resized.crop(crop)
            if ref_w != w_new or ref_h != h_new:
                raise ValueError(f"additional reference image size ({ref_w}x{ref_h}) does not match the base image size ({w_new}x{h_new}).")
        return img_resized
    
    
    @torch.inference_mode()
    def generate_image(
        self,
        prompt,
        negative_prompt,
        ref_images,
        num_steps,
        cfg_guidance,
        seed,
        num_samples=1,
        init_image=None,
        image2image_strength=0.0,
        show_progress=False,
        size_level=512,
        additional_prompt=None,
        additional_ref_images=None,
    ):
        assert num_samples == 1, "num_samples > 1 is not supported yet."
        # 0. check if additional reference images are used
        use_add_ref_img = True if additional_ref_images is not None and additional_prompt is not None else False
            
        
        ref_images_raw, img_info = self.input_process_image(ref_images, img_size=size_level)        
        width, height = ref_images_raw.width, ref_images_raw.height
        ref_images_raw = self.load_image(ref_images_raw)
        ref_images_raw = ref_images_raw.to(self.device)
        
        """
           2025-05-27
            TODO:
                - add support for multiple ref_images
                  - how to handle images with different sizes?
                    (1) resize face reference like in the original code -> padding or cropping side to fit the 1st reference image
                - add support for multiple prompts
                  - When multiple prompts are received, each one should explicitly state what the current reference stands for.
        """
        # 1. encode reference images
        if use_add_ref_img:
            additional_ref_images_raw, additional_img_info = self.input_process_image(additional_ref_images, img_size=size_level)            
            additional_ref_images_raw = self.load_image(additional_ref_images_raw)
            additional_ref_images_raw = additional_ref_images_raw.to(self.device)
        
        if self.offload:
            self.ae = self.ae.to(self.device)
        ref_images = self.ae.encode(ref_images_raw.to(self.device) * 2 - 1)

        # 2. encode additional reference images
        additional_ref_images = None
        if use_add_ref_img:
            additional_ref_images = self.ae.encode(additional_ref_images_raw.to(self.device) * 2 - 1)
        
        if self.offload:
            self.ae = self.ae.cpu()
            cudagc()

        seed = int(seed)
        seed = torch.Generator(device="cpu").seed() if seed < 0 else seed

        t0 = time.perf_counter()

        if init_image is not None:
            init_image = self.load_image(init_image)
            init_image = init_image.to(self.device)
            init_image = torch.nn.functional.interpolate(init_image, (height, width))
            if self.offload:
                self.ae = self.ae.to(self.device)
            init_image = self.ae.encode(init_image.to() * 2 - 1)
            if self.offload:
                self.ae = self.ae.cpu()
                cudagc()
        
        x = torch.randn(
            num_samples,
            16,
            height // 8,
            width // 8,
            device=self.device,
            dtype=torch.bfloat16,
            generator=torch.Generator(device=self.device).manual_seed(seed),
        )

        timesteps = sampling.get_schedule(
            num_steps, x.shape[-1] * x.shape[-2] // 4, shift=True
        )

        if init_image is not None:
            t_idx = int((1 - image2image_strength) * num_steps)
            t = timesteps[t_idx]
            timesteps = timesteps[t_idx:]
            x = t * x + (1.0 - t) * init_image.to(x.dtype)

        x = torch.cat([x, x], dim=0)
        ref_images = torch.cat([ref_images, ref_images], dim=0)
        ref_images_raw = torch.cat([ref_images_raw, ref_images_raw], dim=0)
        inputs = self.prepare([prompt, negative_prompt], x, ref_image=ref_images, ref_image_raw=ref_images_raw)
        with torch.autocast(device_type=self.device.type, dtype=torch.bfloat16):
            x = self.denoise(
                **inputs,
                cfg_guidance=cfg_guidance,
                timesteps=timesteps,
                show_progress=show_progress,
                timesteps_truncate=1.0,
            )
            x = self.unpack(x.float(), height, width)
            if self.offload:
                self.ae = self.ae.to(self.device)
            x = self.ae.decode(x)
            if self.offload:
                self.ae = self.ae.cpu()
                cudagc()
            x = x.clamp(-1, 1)
            x = x.mul(0.5).add(0.5)

        t1 = time.perf_counter()
        print(f"Done in {t1 - t0:.1f}s.")
        for img in x.float():
            image = self.output_process_image(F.to_pil_image(img), img_info)
            img = self.pil2tensor(image)
            break
        return img

MODELS_DIR = os.path.join(folder_paths.models_dir, "MLLM")
if "MLLM" not in folder_paths.folder_names_and_paths:
    current_paths = [MODELS_DIR]
else:
    current_paths, _ = folder_paths.folder_names_and_paths["MLLM"]
folder_paths.folder_names_and_paths["MLLM"] = (current_paths, folder_paths.supported_pt_extensions)

MODELS_DIR = os.path.join(folder_paths.models_dir, "Step1x-Edit")
if "Step1x-Edit" not in folder_paths.folder_names_and_paths:
    current_paths = [MODELS_DIR]
else:
    current_paths, _ = folder_paths.folder_names_and_paths["Step1x-Edit"]
folder_paths.folder_names_and_paths["Step1x-Edit"] = (current_paths, folder_paths.supported_pt_extensions)

class Step1XEditNode:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "image": ("IMAGE", ),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True, "tooltip": "The random seed for generation."}),
                "cfg": ("FLOAT", {"default": 6.0, "min": 0.0, "max": 100.0, "step":0.1, "round": 0.01}),
                "size_level": ("INT", {"default": 512, "min": 0, "max": 32768}),
                "num_steps": ("INT", {"default": 20, "min": 0, "max": 10000, "tooltip": "The number of diffusion steps."}),
                "step1x_edit_model":(folder_paths.get_filename_list("Step1x-Edit"), {"default": "step1x-edit-i1258-FP8.safetensors"}),
                "step1x_edit_model_vae": (folder_paths.get_filename_list("Step1x-Edit"), {"default": "vae.safetensors"}),
                "mllm_model": (os.listdir(folder_paths.get_folder_paths("MLLM")[0]),),
                "offload": ("BOOLEAN", {"default": True, "tooltip": "Enable offloading the model to CPU."}),
                "quantized": ("BOOLEAN", {"default": True, "tooltip": "Enable quantization of the dit."}),
            }
        }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "Step1XEdit"

    @torch.inference_mode()
    def Step1XEdit(self, image, prompt, seed, cfg, size_level, num_steps, step1x_edit_model, step1x_edit_model_vae, mllm_model, offload, quantized):
        image_edit = ImageGenerator(
            ae_path=step1x_edit_model_vae,
            dit_path=step1x_edit_model,
            qwen2vl_model_path=os.path.join(folder_paths.get_folder_paths("MLLM")[0], mllm_model),
            max_length=640,
            offload=offload,
            quantized=quantized
        )
        
        image = image_edit.generate_image(
            prompt,
            negative_prompt="",
            ref_images=image,
            num_samples=1,
            num_steps=num_steps,
            cfg_guidance=cfg,
            seed=seed,
            show_progress=True,
            size_level=size_level,
        ) # This is a PIL Image, but you need a resized tensor as an output. Can we optimize function? Absolutely yes but not now.
        
        return (image, );

class Step1XEditLoader:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "step1x_edit_model":(folder_paths.get_filename_list("Step1x-Edit"), {"default": "step1x-edit-i1258-FP8.safetensors"}),
                "step1x_edit_model_vae": (folder_paths.get_filename_list("Step1x-Edit"), {"default": "vae.safetensors"}),
                "mllm_model": (os.listdir(folder_paths.get_folder_paths("MLLM")[0]),),
                "offload": ("BOOLEAN", {"default": True, "tooltip": "Enable offloading the model to CPU."}),
                "quantized": ("BOOLEAN", {"default": True, "tooltip": "Enable quantization of the dit."}),
            }
        }
    
    RETURN_TYPES = ("Step1XEdit",)
    FUNCTION = "load_from_paths"
    CATEGORY = "Intellicode/Step1X-Edit"

    @classmethod
    def load_from_paths(self, step1x_edit_model, step1x_edit_model_vae, mllm_model, offload, quantized):
        
        step1x_edit = ImageGenerator(
            ae_path=step1x_edit_model_vae,
            dit_path=step1x_edit_model,
            qwen2vl_model_path=os.path.join(folder_paths.get_folder_paths("MLLM")[0], mllm_model),
            max_length=640,
            offload=offload,
            quantized=quantized
        )        
        return (step1x_edit, )

class Step1XEditGenerator:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "image": ("IMAGE", ),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True, "tooltip": "The random seed for generation."}),
                "cfg": ("FLOAT", {"default": 6.0, "min": 0.0, "max": 100.0, "step":0.1, "round": 0.01}),
                "size_level": ("INT", {"default": 512, "min": 0, "max": 32768}),
                "num_steps": ("INT", {"default": 20, "min": 0, "max": 10000, "tooltip": "The number of diffusion steps."}),
                "step1x_edit":("Step1XEdit",),
            }
        }
    
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "generate_image"
    CATEGORY = "Intellicode/Step1X-Edit"

    @torch.inference_mode()
    def generate_image(self, image, prompt, seed, cfg, size_level, num_steps, step1x_edit):
        
        image = step1x_edit.generate_image(
            prompt,
            negative_prompt="",
            ref_images=image,
            num_samples=1,
            num_steps=num_steps,
            cfg_guidance=cfg,
            seed=seed,
            show_progress=True,
            size_level=size_level,
        )
        return (image, )