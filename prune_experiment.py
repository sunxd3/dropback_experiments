import math

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

from models import PruneModel
from datamodules import cifar100_datamodule

def main():
    rank_zero_info(f"Experiment name is: prune")

    tune_asha(num_samples=1, num_epochs=1000, gpus_per_trial=1)

def training(config, num_epochs=10, num_gpus=0):
    deterministic = False
    if deterministic:
        seed_everything(42, workers=True)

    training_labels = (30, 67, 62, 10, 51, 22, 20, 24, 97, 76)
    cifar100_dm = cifar100_datamodule(labels=training_labels, already_prepared=True)
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
                    "sparsity": "sparsity"
                },
                on="validation_end"),
            ModelCheckpoint(
                filename='epoch{epoch:02d}-val_accuracy{ptl/val_accuracy_top1:.2f}-val_loss{ptl/val_loss:.2f}_sparsity{sparsity:.2f}',
                auto_insert_metric_name=False,
                every_n_val_epochs=50,
                save_top_k=10,
                monitor="ptl/val_accuracy_top1",
                mode="max"
                ),
            ModelPruning(
                pruning_fn='l1_unstructured',
                parameter_names=["weight", "bias"],
                make_pruning_permanent=False,
                amount = lambda epoch: 0.1 if epoch % 100 == 0 else 0,
                use_global_unstructured=True,
                verbose=1,
                )
        ]
    )

    checkpoint_path = "/data/sunxd/dropback_experiments/checkpoints/prune-val_accuracy0.80-val_loss1.43_sparsity0.61.ckpt"
    if checkpoint_path:
        model = PruneModel.load_from_checkpoint(checkpoint_path, config=config, num_classes=num_classes, strict=False)  
        rank_zero_info(f"Checkpoint {checkpoint_path} loaded.")
    else:
        model = PruneModel(config=config, num_classes=num_classes)

    trainer.fit(model, datamodule=cifar100_dm) 

def tune_asha(num_samples=10, num_epochs=10, gpus_per_trial=0):
    config = {
        "lr": 0.1,
        "momentum": 0.9,
        "weight_decay": 4e-5,
    }

    scheduler = ASHAScheduler(
        max_t=num_epochs,
        grace_period=20,
        reduction_factor=2)

    in_jupyter_notebook = False
    if in_jupyter_notebook:
        reporter = JupyterNotebookReporter(
            overwrite=False,
            parameter_columns=["lr", "momentum"],
            metric_columns=["loss", "mean_accuracy", "training_iteration", "current_lr"]
        )
    else:
        reporter = CLIReporter(
            parameter_columns=["lr", "momentum"],
            metric_columns=["loss", "mean_accuracy", "training_iteration", "current_lr"])

    analysis = tune.run(
        tune.with_parameters(
            training,
            num_epochs=num_epochs,
            num_gpus=gpus_per_trial,
        ),
        resources_per_trial={
            "cpu": 2,
            "gpu": gpus_per_trial
        },
        metric="loss",
        mode="min",
        config=config,
        num_samples=num_samples,
        # scheduler=scheduler,
        progress_reporter=reporter,
        name="prune")

    print("Best hyperparameters found were: ", analysis.best_config)

if __name__ == '__main__':
    main()