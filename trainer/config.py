from typing import Union, List, Optional
from datetime import datetime
from pydantic import BaseModel
import json, time, os
from typing import Literal
from trainer.models import pretrained_models
from trainer.utils.utils import pick_best_gpu_id

class TrainingConfig(BaseModel):
    lora_training_urls: str
    concept_mode: Literal["face", "style", "object"]
    caption_prefix: str = ""      # hardcoding this will inject TOK manually and skip the chatgpt token injection step, not recommended unless you know what you're doing
    caption_model: Literal["gpt4-v", "blip"] = "blip"
    sd_model_version: Literal["sdxl", "sd15", "sd3"]
    pretrained_model: dict = None
    seed: Union[int, None] = None
    resolution: int = 512
    validation_img_size: Optional[Union[int, List[int]]] = None   # [width, height], target_n_pixels ** 0.5 or None
    train_img_size: List[int] = None
    train_aspect_ratio: float = None
    train_batch_size: int = 4
    num_train_epochs: int = 10000
    max_train_steps: int = 360
    checkpointing_steps: int = 10000
    gradient_accumulation_steps: int = 1
    is_lora: bool = True

    unet_optimizer_type: Literal["adamw", "prodigy"] = "adamw"
    unet_lr_warmup_steps: int = None  # slowly increase the learning rate of the adamw unet optimizer
    unet_lr: float = 1.0e-3
    prodigy_d_coef: float = 1.0
    unet_prodigy_growth_factor: float = 1.05  # lower values make the lr go up slower (1.01 is for 1k step runs, 1.02 is for 500 step runs)
    lora_weight_decay: float = 0.002
    # if ti_lr is None, then we completely skip textual inversion
    ti_lr: Union[float, None] = 1e-3
    ti_lr_warmup_steps: int = 20   # slowly ramp up the learning rate to build some momentum
    token_warmup_steps: int = 0    #  warmup the token embeddings with a pure txt loss
    ti_weight_decay: float = 0.0
    ti_optimizer: Literal["adamw", "prodigy"] = "adamw"
    freeze_ti_after_completion_f: float = 1.0   # freeze the TI after this fraction of the training is done
    
    cond_reg_w: float = 0.0e-5
    tok_cond_reg_w: float = 0.0e-5
    tok_cov_reg_w: float = 2000.    # regularizes the token covariance matrix wrt pretrained "healthy" tokens
    off_ratio_power: float = 0.02   # Pulls the std of the token distribution towards the target std
    l1_penalty: float = 0.01        # Makes the unet lora matrix more sparse
    
    noise_offset: float = 0.02      # Noise offset training to improve very dark / very bright images
    snr_gamma: float = 5.0
    lora_alpha_multiplier: float = 1.0
    lora_rank: int = 12
    use_dora: bool = False

    left_right_flip_augmentation: bool = True
    augment_imgs_up_to_n: int = 40
    mask_target_prompts: Union[None, str] = None
    crop_based_on_salience: bool = True
    use_face_detection_instead: bool = False  # use a different model (not CLIPSeg) to generate face masks
    clipseg_temperature: float = 0.5   # temperature for the CLIPSeg mask
    n_sample_imgs: int = 4
    name: str = None
    output_dir: str = "lora_models/unnamed"
    debug: bool = False
    allow_tf32: bool = True
    remove_ti_token_from_prompts: bool = False
    weight_type: Literal["fp16", "bf16", "fp32"] = "bf16"
    n_tokens: int = 2
    inserting_list_tokens: List[str] = ["<s0>","<s1>"]
    token_dict: dict = {"TOK": "<s0><s1>"}
    device: str = "cuda:0"
    crops_coords_top_left_h: int = 0
    crops_coords_top_left_w: int = 0
    do_cache: bool = True
    unet_learning_rate: float = 1.0
    lr_num_cycles: int = 1
    lr_power: float = 1.0
    sample_imgs_lora_scale: float = 0.65    # Default lora scale for sampling the validation images
    dataloader_num_workers: int = 0
    training_attributes: dict = {}
    aspect_ratio_bucketing: bool = False
    start_time: float = 0.0
    job_time: float = 0.0
    """
    For text encoder lora training, the trigger variable is: text_encoder_lora_optimizer
    if text_encoder_lora_optimizer is not None then everything else is used. 
    Else the other variables are ignored.
    """
    text_encoder_lora_optimizer: Union[None, Literal["adamw"]] = None
    text_encoder_lora_lr: float = 1.0e-5
    txt_encoders_lr_warmup_steps: int = 200
    text_encoder_lora_weight_decay: float = 1.0e-5
    text_encoder_lora_rank: int = 16

    def __init__(self, **data):
        super().__init__(**data)
        self.pretrained_model = pretrained_models[self.sd_model_version]

        # add some metrics to the foldername:
        lora_str = "dora" if self.use_dora else "lora"
        timestamp_short = datetime.now().strftime("%d_%H-%M-%S")
        
        if not self.name:
            self.name = f"{os.path.basename(self.output_dir)}_{self.concept_mode}_{lora_str}_{self.sd_model_version}_{timestamp_short}"

        self.output_dir = self.output_dir + f"--{timestamp_short}-{self.sd_model_version}_{self.concept_mode}_{lora_str}_{self.resolution}_{self.prodigy_d_coef}_{self.caption_model}_{self.max_train_steps}"
        os.makedirs(self.output_dir, exist_ok=True)

        if self.seed is None:
            self.seed = int(time.time())

        if self.unet_lr_warmup_steps is None:
            self.unet_lr_warmup_steps = self.max_train_steps

        if self.concept_mode == "face":
            print(f"Face mode is active ----> disabling left-right flips and setting mask_target_prompts to 'face'.")
            self.left_right_flip_augmentation = False  # always disable lr flips for face mode!
            self.mask_target_prompts = "face"
            #self.use_face_detection_instead = True
        
        if self.use_dora:
            print(f"Disabling L1 penalty and LoRA weight decay for DORA training.")
            self.l1_penalty = 0.0
            self.lora_weight_decay = 0.0
            self.text_encoder_lora_weight_decay = 0.0

        # build the inserting_list_tokens and token dict using n_tokens:
        inserting_list_tokens = [f"<s{i}>" for i in range(self.n_tokens)]
        self.inserting_list_tokens = inserting_list_tokens
        self.token_dict = {"TOK": "".join(inserting_list_tokens)}

        gpu_id = pick_best_gpu_id()
        self.device = f'cuda:{gpu_id}'
        self.start_time = time.time()

    @classmethod
    def from_json(cls, file_path: str):
        with open(file_path, 'r') as f:
            data = json.load(f)

        return cls(**data)
    
    def save_as_json(self, file_path: str) -> None:
        with open(file_path, 'w') as f:
            json.dump(self.dict(), f, indent=4)