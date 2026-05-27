#!/bin/bash
#SBATCH --job-name=vllm_server
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --output=vllm_server_%j.log

module purge
module load 2023
module load CUDA/12.1.1
module load Miniconda3/23.5.2-0

export HF_HOME=/scratch-shared/$USER/.cache/huggingface

source activate vllm_env

# Write node name so you know where to tunnel
echo "Node:   $SLURMD_NODENAME"  > vllm_node.txt
echo "Job ID: $SLURM_JOB_ID"   >> vllm_node.txt
echo ""                         >> vllm_node.txt
echo "Run this on your laptop:" >> vllm_node.txt
echo "ssh -L 8000:$SLURMD_NODENAME:8000 $USER@snellius.surf.nl -N" >> vllm_node.txt
cat vllm_node.txt

python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2-VL-7B-Instruct \
    --port 8000 \
    --host 0.0.0.0 \
    --max-model-len 4096
