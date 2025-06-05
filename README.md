# ComfyUI-Step1X-Edit

ComfyUI Custom Node for [Step1X-Edit](https://github.com/stepfun-ai/Step1X-Edit/). Noted this node may consume large VRAMs!

---

This repository is a modified version of [original project URL](https://github.com/quank123wip/ComfyUI-Step1X-Edit).
Modifications were made by wonbinK in 2025.

## Modification
- Split node into (```Step 1X Edit Loader```) Loader and (```Step 1X Edit Generator```) Generator 
- You can find those nodes in intellicode/Step1X-Edit (ComfyUI node)

---

## Installation

1. Navigate to your ComfyUI's custom_nodes directory:
```bash
cd ComfyUI/custom_nodes
```

2. Clone this repository:
```bash
git clone https://github.com/intellicodeinc/ComfyUI-Step1X-Edit
```

3. Install requirements:
```bash
cd ComfyUI-Step1X-Edit
pip install -r requirements.txt
```

Please noted this project requires [flash attention](https://github.com/Dao-AILab/flash-attention), but build it using pip consumes lots of time. Try to build it manually or use prebuilt wheel according to [original guideline](https://github.com/stepfun-ai/Step1X-Edit/). If you have problem building it, I suggest you to install torch<=2.7 and use the latest prebuilt wheel from [flash attention prebuilt wheel](https://github.com/Dao-AILab/flash-attention/releases)

```bash
# examples
# python 3.12
# this package is for Pytorch 2.6 but pytorch 2.7 is also ok
pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.6cxx11abiTRUE-cp312-cp312-linux_x86_64.whl
``` 


### Or Install via ComfyUI Manager

## Usage

Download the safetensors weight of the pretrained model from [here](https://huggingface.co/stepfun-ai/Step1X-Edit), and then place them in `models/Step1x-Edit` (both the vae and model itself).

Download the vision encoder(Qwen/Qwen2.5-VL-7B-Instruct) weight from [here](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct), and place the whole folder inside `models/MLLM`.

## Citation

```bibtex
@article{liu2025step1x-edit,
      title={Step1X-Edit: A Practical Framework for General Image Editing}, 
      author={Shiyu Liu and Yucheng Han and Peng Xing and Fukun Yin and Rui Wang and Wei Cheng and Jiaqi Liao and Yingming Wang and Honghao Fu and Chunrui Han and Guopeng Li and Yuang Peng and Quan Sun and Jingwei Wu and Yan Cai and Zheng Ge and Ranchen Ming and Lei Xia and Xianfang Zeng and Yibo Zhu and Binxing Jiao and Xiangyu Zhang and Gang Yu and Daxin Jiang},
      journal={arXiv preprint arXiv:2504.17761},
      year={2025}
}
```

## Disclaimer

The results produced by this image editing model are entirely determined by user input and actions. The development team and this open-source project are not responsible for any outcomes or consequences arising from its use.

## License

MIT License

