# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/core.ipynb.

# %% auto 0
__all__ = ['HF_SD_MODEL', 'HF_CLIP_MODEL', 'VAE_ENCODE_SCALE', 'VAE_DECODE_SCALE', 'lms_scheduler', 'euler_a_scheduler',
           'SCHEDULERS', 'DEFAULT_SCHEDULER', 'vae', 'tokenizer', 'text_encoder', 'unet', 'scheduler', 'generator',
           'i2i_pipe', 'pil_to_latents', 'latents_to_pil', 'generate_image_grid', 'get_image_size_from_aspect_ratio',
           'pil_to_b64', 'b64_to_pil', 'get_pipe_params_from_airt_req', 'Config', 'AIrtRequest', 'AIrtResponse',
           'text2image', 'image2image', 'handle_airt_request']

# %% ../nbs/core.ipynb 4
import torch
from torch import autocast
from torchvision import transforms as tfms

from transformers import CLIPTextModel, CLIPTokenizer
from transformers import logging

from diffusers.models import AutoencoderKL, UNet2DConditionModel
from diffusers.schedulers import (
    EulerAncestralDiscreteScheduler, 
    LMSDiscreteScheduler
)
from diffusers.pipelines.stable_diffusion.pipeline_stable_diffusion import (
    StableDiffusionPipelineOutput, 
    StableDiffusionPipeline,
)
from diffusers.pipelines.stable_diffusion.pipeline_stable_diffusion_img2img import StableDiffusionImg2ImgPipeline

import io
import inspect
import requests
import random
from tqdm.auto import tqdm
import PIL
from matplotlib import pyplot as plt
import numpy as np
import base64
from typing import List, Union, Tuple, Dict, Any
from pprint import pprint

# serving
import pydantic

logging.set_verbosity_error()

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    # https://huggingface.co/docs/diffusers/optimization/mps
    device = "mps"
else:
    device = "cpu"
  

# %% ../nbs/core.ipynb 6
HF_SD_MODEL = "runwayml/stable-diffusion-v1-5"
HF_CLIP_MODEL = "openai/clip-vit-large-patch14"

VAE_ENCODE_SCALE = 0.18215
VAE_DECODE_SCALE = 1 / VAE_ENCODE_SCALE

# %% ../nbs/core.ipynb 8
# CUSTOM_PARAMS_START = "custom_params_start"

# %% ../nbs/core.ipynb 11
lms_scheduler = LMSDiscreteScheduler(
    beta_start=0.00085, 
    beta_end=0.012, 
    beta_schedule="scaled_linear", 
    num_train_timesteps=1000
)

euler_a_scheduler = EulerAncestralDiscreteScheduler.from_config(
    HF_SD_MODEL, subfolder="scheduler"
)

# %% ../nbs/core.ipynb 12
SCHEDULERS = {
    "euler_a": euler_a_scheduler,
    "lms": lms_scheduler,
}

DEFAULT_SCHEDULER = euler_a_scheduler

# %% ../nbs/core.ipynb 14
if device == "mps":
    pipe = StableDiffusionPipeline.from_pretrained(
        HF_SD_MODEL, 
        scheduler=DEFAULT_SCHEDULER
    )
else:
    pipe = StableDiffusionPipeline.from_pretrained(
        HF_SD_MODEL, 
        torch_dtype=torch.float16, 
        revision="fp16",
        scheduler=DEFAULT_SCHEDULER
    )

pipe.safety_checker = None
pipe.to(device)
# pipe.enable_xformers_memory_efficient_attention()
pipe.enable_attention_slicing()

# %% ../nbs/core.ipynb 16
vae = pipe.vae
tokenizer = pipe.tokenizer
text_encoder = pipe.text_encoder
unet = pipe.unet
scheduler = pipe.scheduler
generator = torch.Generator()

# %% ../nbs/core.ipynb 19
i2i_pipe = StableDiffusionImg2ImgPipeline(**pipe.components)

# %% ../nbs/core.ipynb 24
@torch.no_grad()
def pil_to_latents(im: PIL.Image.Image) -> torch.Tensor:
    """
    Transform single image into single latent in a batch w/ shape = (1, 4, 64, 64)
    """
    device = vae.device.type
    tensor = tfms.ToTensor()(im).unsqueeze(0).to(device)
    tensor = tensor * 2 - 1
    with torch.autocast(device):
        latent = vae.encode(tensor) 
    return VAE_ENCODE_SCALE * latent.latent_dist.sample()

# %% ../nbs/core.ipynb 27
@torch.no_grad()
def latents_to_pil(latents: torch.Tensor) -> List[PIL.Image.Image]:
    """
    Transform batch of latent back to list of pil images
    - `latents`: shape(batch_size, channels, heights, width)
    """
    
    device = vae.device.type

    latents = latents.to(device)
    latents = VAE_DECODE_SCALE * latents
    
    with torch.autocast(device):
        ims = vae.decode(latents).sample
        
    ims = (ims / 2 + 0.5).clamp(0, 1)
    ims = ims.detach().cpu().permute(0, 2, 3, 1).numpy()
    ims = (ims * 255).round().astype("uint8")
    pil_ims = [PIL.Image.fromarray(im) for im in ims]
    return pil_ims

# %% ../nbs/core.ipynb 30
def generate_image_grid(
    images: List[PIL.Image.Image], 
    nrow: int, 
    ncol: int):

    w, h = images[0].size # assume all images are of the same size
    grid = PIL.Image.new('RGB', size=(ncol * w, nrow * h))
    for i, im in enumerate(images): 
        grid.paste(im, box=(i % ncol * w, i // ncol * h))
    return grid

# %% ../nbs/core.ipynb 33
def get_image_size_from_aspect_ratio(aspect_ratio: float) -> Tuple[int, int]:
    base = 512
    width, height = (base, base)
    
    if aspect_ratio == 1:
        pass
    elif aspect_ratio < 1:
        height = base
        raw_width = round(height * aspect_ratio)
        multiplier = raw_width // 8
        width = 8 * multiplier
    elif aspect_ratio > 1:
        width = base
        raw_height = round(width / aspect_ratio)
        multiplier = raw_height // 8
        height = 8 * multiplier
    
    return (width, height)

# %% ../nbs/core.ipynb 36
def pil_to_b64(im: PIL.Image.Image, format="PNG") -> str:
    buffered = io.BytesIO()
    im.save(buffered, format=format)
    im_str = base64.b64encode(buffered.getvalue())
    return im_str.decode()

# %% ../nbs/core.ipynb 38
def b64_to_pil(b64: str, format="PNG") -> PIL.Image.Image:
    im = PIL.Image.open(io.BytesIO(base64.b64decode(b64)))
    try:
        im = im.convert(format)
    except ValueError:
        im = im.convert("RGB")
    
    return im

# %% ../nbs/core.ipynb 41
def get_pipe_params_from_airt_req(req: AIrtRequest, pipe: StableDiffusionPipeline) -> dict:
    pipe_accepted_param_keys = inspect.signature(pipe).parameters.keys()
    pipe_params = {
        k: v for k, v in req.__dict__.items()
        if k in pipe_accepted_param_keys
    }
    return pipe_params

# %% ../nbs/core.ipynb 44
class Config:
    arbitrary_types_allowed = True

@pydantic.dataclasses.dataclass(config=Config)
class AIrtRequest:
    # inherit from `StableDiffusionPipeline`
    prompt: Union[str, List[str]]
    height: int = 512
    width: int = 512
    num_inference_steps: int = 30
    guidance_scale: float = 7.5
    negative_prompt: Union[List[str], str, None] = None
    num_images_per_prompt: Union[int, None] = 1
    eta: float = 0.0
        
    # inherif from `StableDiffusionImg2ImgPipeline`
    init_image: Union[torch.FloatTensor, PIL.Image.Image, str] = None # support b64
    strength: float = 0.8
    
    # custom parameters
    mode: str = "text2image"
    cmd: str = None
    steps: int = 0
    cfg: float = None
    batch_size: int = None
    seed: int = None
    aspect_ratio: float = 1
    scheduler: str = "euler_a"
    
    # https://pydantic-docs.helpmanual.io/usage/validators/
    @pydantic.validator('steps')
    def steps_must_be_at_least_one(cls, v):
        if not v:
            return v
        
        if v < 1:
            raise ValueError("steps must be at least 1")
        return v
    
    
    @pydantic.validator('scheduler')
    def scheduler_must_be_available(cls, v):
        if v and not v in SCHEDULERS:
            raise ValueError(f"{v} is not a valid key in {SCHEDULERS.keys()}")
        return v.lower().strip()
    
    
    # https://pydantic-docs.helpmanual.io/usage/dataclasses/#initialize-hooks
    def __post_init_post_parse__(self, **kwargs):
        param_alias = {
            "steps": "num_inference_steps",
            "batch_size": "num_images_per_prompt",
            "cfg": "guidance_scale",
        }
        
        for custom_p, pipe_p in param_alias.items():
            custom_v = getattr(self, custom_p)
            pipe_v = getattr(self, pipe_p)
            
            if custom_v and custom_v != pipe_v:
                setattr(self, pipe_p, custom_v)
                
        # aspect ratio
        ar = self.aspect_ratio
        if ar != 1:
            width, height = get_image_size_from_aspect_ratio(ar)
            self.width = width
            self.height = height    
            
        # init_image for i2i
        if isinstance(self.init_image, str):
            self.init_image = b64_to_pil(self.init_image)
        

# %% ../nbs/core.ipynb 47
@pydantic.dataclasses.dataclass
class AIrtResponse:
    images: List[str]
    seed: int = None
        
    def keys(self) -> dict:
        return self.__dict__.keys()

# %% ../nbs/core.ipynb 50
async def text2image(
    req: AIrtRequest, 
    return_pipe_out=False, 
    print_req=True
) -> Union[StableDiffusionPipelineOutput, AIrtResponse]:
    if print_req:
        pprint(req)
    
    generator = torch.Generator()
    seed = req.seed if req.seed else generator.seed()
    generator = torch.manual_seed(seed)
    
    scheduler_name = req.scheduler
    if scheduler_name:
        pipe.scheduler = SCHEDULERS[scheduler_name]
    
    pipe_params = get_pipe_params_from_airt_req(req, pipe)
    pipe_out = pipe(**pipe_params)
    
    # set back to default scheduler
    pipe.scheduler = DEFAULT_SCHEDULER
        
    b64images = [pil_to_b64(im) for im in pipe_out.images]
    
    if return_pipe_out:
        return pipe_out
    else:
        return AIrtResponse(
            images=b64images,
            seed=seed,
        )


# %% ../nbs/core.ipynb 55
async def image2image(
    req: AIrtRequest, 
    return_pipe_out=False,
    print_req=True
) -> Union[StableDiffusionPipelineOutput, AIrtResponse]:
    if print_req:
        pprint(req)
        
    generator = torch.Generator()
    seed = req.seed if req.seed else generator.seed()
    generator = torch.manual_seed(seed)
    
    scheduler_name = req.scheduler
    if scheduler_name:
        pipe.scheduler = SCHEDULERS[scheduler_name]
    
    pipe_params = get_pipe_params_from_airt_req(req, i2i_pipe)
    pipe_out = i2i_pipe(**pipe_params)
    
    # set back to default scheduler
    pipe.scheduler = DEFAULT_SCHEDULER
        
    b64images = [pil_to_b64(im) for im in pipe_out.images]
    
    if return_pipe_out:
        return pipe_out
    else:
        return AIrtResponse(
            images=b64images,
            seed=seed,
        )    

# %% ../nbs/core.ipynb 60
async def handle_airt_request(req: AIrtRequest):
    pprint(req)
    mode = req.mode
    
    if mode == "text2image":
        return await text2image(req, print_req=False)
    elif mode == "image2image":
        return await image2image(req, print_req=False)
    else:
        raise NotImplementedError(req.mode)
