rollout_length=16
num_updates_per_rollout=10
batch_size=16
lr_actor=8e-05
lr_critic=1e-04
clip_eps=0.10
lmd=0.96
max_grad_norm=0.5
seed=42
actor_save_path="../../datas/checkpoints"
actor_best_save_name="pre_best.pth"
actor_last_save_name="pre_last.pth"
num_train_steps=1000000
eval_interval=10000
num_eval_episodes=10
agent_name="Agent"
config_path="pretrain_config.json"
variable_ranges_path="variable_ranges.json"
depth_range=0.03
limit_order_range=0.03
max_order_volume=50
short_selling_penalty=0.0
execution_vonus=0.01
agent_trait_memory=0.5
device="cuda:1"
python train_hetero_rl.py \
--rollout_length $rollout_length \
--num_updates_per_rollout $num_updates_per_rollout \
--batch_size $batch_size \
--lr_actor $lr_actor \
--lr_critic $lr_critic \
--clip_eps $clip_eps \
--lmd $lmd \
--max_grad_norm $max_grad_norm \
--seed $seed \
--num_train_steps $num_train_steps \
--eval_interval $eval_interval \
--num_eval_episodes $num_eval_episodes \
--depth_range $depth_range \
--limit_order_range $limit_order_range \
--max_order_volume $max_order_volume \
--short_selling_penalty $short_selling_penalty \
--execution_vonus $execution_vonus \
--agent_trait_memory $agent_trait_memory \
--config_path $config_path \
--variable_ranges_path $variable_ranges_path \
--actor_save_path $actor_save_path \
--actor_best_save_name $actor_best_save_name \
--actor_last_save_name $actor_last_save_name \
--agent_name $agent_name \
--device $device