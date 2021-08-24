from pathlib import Path

import math

import torch

import pytorch_lightning as pl
from pytorch_lightning import seed_everything
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning.callbacks import ModelPruning
from pytorch_lightning.utilities.cloud_io import load as pl_load
from pytorch_lightning.utilities import rank_zero_info

from ray import tune
from ray.tune import CLIReporter, JupyterNotebookReporter
from ray.tune.schedulers import ASHAScheduler
from ray.tune.integration.pytorch_lightning import TuneReportCallback

from models import DBModel
from datamodules import cifar100_datamodule

def main():
    rank_zero_info(f"Experiment name is: tl_dropback")

    tune_asha(num_samples=1, num_epochs=400, gpus_per_trial=1)

def training(config, num_epochs=10, num_gpus=0):
    deterministic = False
    if deterministic:
        seed_everything(42, workers=True)

    training_labels = (30, 67, 62, 10, 51, 22, 20, 24, 97, 76)
    target_list = (33, 19, 63, 79, 46, 93, 50, 52, 8, 85)
    target_list_2 = (49, 15, 66, 99, 98, 29, 74, 47, 58, 89)
    cifar100_dm = cifar100_datamodule(labels=target_list, already_prepared=True, data_dir=str(Path.home())+"/data")
    num_classes = cifar100_dm.num_classes
    
    trainer = pl.Trainer(
        max_epochs=num_epochs,
        gpus=math.ceil(num_gpus),           # If fractional GPUs passed in, convert to int.
        logger=TensorBoardLogger(
            save_dir=tune.get_trial_dir(), name="", version="."),
        progress_bar_refresh_rate=0,
        deterministic=deterministic,
        callbacks = [
            TuneReportCallback(
                metrics = {
                    "loss": "ptl/val_loss",
                    "mean_accuracy": "ptl/val_accuracy_top1",
                    "current_lr": "current_lr",
                },
                on="validation_end"),
            ModelCheckpoint(
                monitor='ptl/val_accuracy_top1',
                filename='epoch{epoch:02d}-val_accuracy{ptl/val_accuracy_top1:.2f}-val_loss{ptl/val_loss:.2f}',
                save_top_k=3,
                mode='max',
                auto_insert_metric_name=False
            )
        ]
    )

    # checkpoint_path = "/data/sunxd/dropback_experiments/checkpoints/dropback-val_accuracy0.77-val_loss1.25.ckpt"
    checkpoint_path = "/data/sunxd/dropback_experiments/checkpoints/dropback-val_accuracy0.82-val_loss0.81.ckpt"
    # checkpoint_path = None
    if checkpoint_path:
        checkpoint = torch.load(checkpoint_path)
        model = DBModel(config=config, num_classes=num_classes)
        model.load_state_dict(checkpoint['state_dict'])  
        
        # Clear the momentum related states
        checkpoint['optimizer_states'][0]['state'] = {}
        # Initialize the trainer
        trainer.max_epochs = 0
        trainer.fit(model, datamodule=cifar100_dm)
        trainer.max_epochs = num_epochs
        trainer.optimizers[0].load_state_dict(checkpoint['optimizer_states'][0])

        rank_zero_info(f"Checkpoint {checkpoint_path} loaded.")
    else:
        model = DBModel(config=config, num_classes=num_classes)

    trainer.fit(model, datamodule=cifar100_dm) 

def tune_asha(num_samples=10, num_epochs=10, gpus_per_trial=0):
    config = {
        "lr": 0.123,
        "momentum": 0.826,
        "weight_decay": 4e-5,
        "track_size": 111835,
        "init_decay": 0.994,
        "q": 0.95,
        "q_init": 0.0073,
	    "q_step": 1.49e-6,
        "sf": False
    }

    scheduler = ASHAScheduler(
        max_t=num_epochs,
        grace_period=60,
        reduction_factor=2)

    in_jupyter_notebook = False
    if in_jupyter_notebook:
        reporter = JupyterNotebookReporter(
            overwrite=False,
            parameter_columns=["lr", "momentum", "weight_decay", "q_init", "q_step"],
            metric_columns=["loss", "mean_accuracy", "training_iteration", "current_lr"]
        )
    else:
        reporter = CLIReporter(
            parameter_columns=["lr", "momentum", "weight_decay", "q_init", "q_step"],
            metric_columns=["loss", "mean_accuracy", "training_iteration", "current_lr"])

    analysis = tune.run(
        tune.with_parameters(
            training,
            num_epochs=num_epochs,
            num_gpus=gpus_per_trial,
        ),
        resources_per_trial={
            "cpu": 4,
            "gpu": gpus_per_trial
        },
        metric="loss",
        mode="min",
        config=config,
        num_samples=num_samples,
        scheduler=scheduler,
        progress_reporter=reporter,
        name="tl_dropback")

    print("Best hyperparameters found were: ", analysis.best_config)

if __name__ == '__main__':
    main()