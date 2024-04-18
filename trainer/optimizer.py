from peft import LoraConfig, get_peft_model
import torch
import prodigyopt
from typing import Iterable

def get_unet_optimizer(
    prodigy_d_coef: float,
    lora_weight_decay: float,
    use_dora: bool,
    unet_trainable_params: Iterable,
    optimizer_name="prodigy"
):
    ## unet_trainable_params can be unet.parameters() or a list of lora params

    if optimizer_name == "adamw":
        optimizer_unet = torch.optim.AdamW(unet_trainable_params, lr = 1e-4)
    
    elif optimizer_name == "prodigy":
        # Note: the specific settings of Prodigy seem to matter A LOT
        optimizer_unet = prodigyopt.Prodigy(
            unet_trainable_params,
            d_coef = prodigy_d_coef,
            lr=1.0,
            decouple=True,
            use_bias_correction=True,
            safeguard_warmup=True,
            weight_decay=lora_weight_decay if not use_dora else 0.0,
            betas=(0.9, 0.99),
            #growth_rate=1.025,  # this slows down the lr_rampup
            growth_rate=1.04,  # this slows down the lr_rampup
        )
    else:
        raise NotImplementedError(f"Invalid optimizer_name for unet: {optimizer_name}")

    return optimizer_unet

def get_unet_lora_parameters(
    lora_rank,
    lora_alpha_multiplier: float,
    lora_weight_decay: float,
    use_dora: bool,
    unet,
    pipe,
):
    unet_lora_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_rank * lora_alpha_multiplier,
        init_lora_weights="gaussian",
        target_modules=["to_k", "to_q", "to_v", "to_out.0"],
        #target_modules=["to_v"],
        use_dora=use_dora,
    )

    #unet.add_adapter(unet_lora_config)
    unet = get_peft_model(unet, unet_lora_config)
    pipe.unet = unet
    
    unet_lora_parameters = list(filter(lambda p: p.requires_grad, unet.parameters()))
    unet_trainable_params = [
        {
            "params": unet_lora_parameters,
            "weight_decay": lora_weight_decay if not use_dora else 0.0,
        },
    ]
    return unet, unet_trainable_params, unet_lora_parameters

def get_textual_inversion_optimizer(
    text_encoders: list,
    textual_inversion_lr: float,
    textual_inversion_weight_decay,
    optimizer_name = "prodigy"
):
    text_encoder_parameters = []
    for text_encoder in text_encoders:
        if text_encoder is not  None:
            text_encoder.train()
            for name, param in text_encoder.named_parameters():
                if "token_embedding" in name:
                    param.requires_grad = True
                    text_encoder_parameters.append(param)
                    print(f"Added {name} with shape {param.shape} to the trainable parameters")
                else:
                    pass

    params_to_optimize_ti = [
        {
            "params": text_encoder_parameters,
            "lr": textual_inversion_lr if (optimizer_name != "prodigy") else 1.0,
            "weight_decay":textual_inversion_weight_decay,
        },
    ]

    if optimizer_name == "prodigy":
        optimizer_ti = prodigyopt.Prodigy(
                            params_to_optimize_ti,
                            d_coef = 1.0,
                            lr=1.0,
                            decouple=True,
                            use_bias_correction=True,
                            safeguard_warmup=True,
                            weight_decay=textual_inversion_weight_decay,
                            betas=(0.9, 0.99),
                            #growth_rate=5.0,  # this slows down the lr_rampup
                        )
    elif  optimizer_name == "adamw":
        optimizer_ti = torch.optim.AdamW(
            params_to_optimize_ti,
            weight_decay=textual_inversion_weight_decay,
        )
    else:
        raise NotImplementedError(f"Invalid optimizer_name: '{optimizer_name}'") 
    return optimizer_ti, text_encoder_parameters

def get_text_encoder_lora_parameters(text_encoder, lora_rank, lora_alpha_multiplier, use_dora: bool):
    text_encoder_lora_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_rank * lora_alpha_multiplier,
        init_lora_weights="gaussian",
        target_modules=["k_proj", "q_proj", "v_proj", "out_proj"],
        use_dora=use_dora,
    )
    text_encoder_peft_model = get_peft_model(text_encoder, text_encoder_lora_config)
    text_encoder_lora_params = list(filter(lambda p: p.requires_grad, text_encoder_peft_model.parameters()))
    return text_encoder_peft_model, text_encoder_lora_params

def get_optimizer_and_peft_models_text_encoder_lora(
    text_encoders: list, 
    lora_rank: int, 
    lora_alpha_multiplier: float, 
    use_dora: bool, 
    optimizer_name: str,
    lora_lr: float,
    weight_decay: float
):
    text_encoder_lora_parameters = []
    text_encoder_peft_models = []
    for text_encoder in text_encoders:
        if text_encoder is not None:
            text_encoder_peft_model, text_encoder_lora_params = get_text_encoder_lora_parameters(
                text_encoder=text_encoder,
                lora_rank=lora_rank,
                lora_alpha_multiplier=lora_alpha_multiplier,
                use_dora=use_dora
            )
            text_encoder_lora_parameters.extend(text_encoder_lora_params)
            text_encoder_peft_models.append(text_encoder_peft_model)

    if optimizer_name == "adamw":
        optimizer_text_encoder_lora = torch.optim.AdamW(
                text_encoder_lora_parameters, 
                lr =  lora_lr,
                weight_decay=weight_decay if not use_dora else 0.0
            )
    else:
        raise NotImplementedError(f"Text encoder LoRA finetuning is not yet implemented for optimizer: {optimizer_name}")

    return optimizer_text_encoder_lora, text_encoder_peft_models


class OptimizerCollection:
    def __init__(
        self,
        optimizer_unet = None,
        optimizer_textual_inversion = None,
        optimizer_text_encoder_lora = None
    ):
        """
        run operations on all the relevant optimizers with a single function call
        """
        self.optimizer_unet=optimizer_unet
        self.optimizer_textual_inversion=optimizer_textual_inversion
        self.optimizer_text_encoder_lora=optimizer_text_encoder_lora
        
        self.optimizers = [
            self.optimizer_unet,
            self.optimizer_textual_inversion,
            self.optimizer_text_encoder_lora
        ]

    def zero_grad(self):
        for optimizer in self.optimizers:
            if optimizer is not None:
                optimizer.zero_grad()
    
    def step(self):
        for optimizer in self.optimizers:
            if optimizer is not None:
                optimizer.step()