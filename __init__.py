# -*- coding: utf-8 -*-
"""
__init__.py

Description:
    This is the __init__.py file for the ComfyUI-Step1X-Edit custom node.

    From the original repo, there is one node:
        - Step-1XEditNode: The main node for the Step 1X Edit model (loader + generator).

    We add the following nodes:
        - Step-1XEditLoader: A loader for the Step 1X Edit model. (loader only)
        - Step-1XEditGenerator: A generator for the Step 1X Edit model. (generator only)

Author: Wonbim Kim
Created At: 2025-05-28
Email: wbkim@intellicode.co.kr
"""


from .step1xeditnode import Step1XEditNode
from .step1xeditnode import Step1XEditLoader, Step1XEditGenerator

NODE_CLASS_MAPPINGS = {
    "Step-1XEditNode": Step1XEditNode,
    "Step-1XEditLoader": Step1XEditLoader,
    "Step-1XEditGenerator": Step1XEditGenerator,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Step-1XEditNode": "Step 1X Edit Node",
    "Step-1XEditLoader": "Step 1X Edit Loader",
    "Step-1XEditGenerator": "Step 1X Edit Generator",
}
