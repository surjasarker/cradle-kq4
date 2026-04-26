#!/bin/bash
#SBATCH --job-name=install_vllm
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --time=00:30:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=install_vllm_%j.log

module purge
module load 2023
module load CUDA/12.1.1
module load Miniconda3/23.5.2-0

export HF_HOME=/scratch-shared/$USER/.cache/huggingface
mkdir -p $HF_HOME

# Create a Python 3.11 env (only runs if it doesn't exist yet)
conda create -n vllm_env python=3.11 -y

source activate vllm_env

pip install vllm --quiet

# Pre-download the model
python -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen2-VL-7B-Instruct')
print('Model downloaded.')
"
