import copy
import time
from typing import Any, Dict, Tuple

import chex
import flax
import hydra
import jax
import jax.numpy as jnp
import optax
from colorama import Fore, Style
from flax.core.frozen_dict import FrozenDict
from jumanji.env import Environment
from omegaconf import DictConfig, OmegaConf
from rich.pretty import pprint

from stoix.base_types import (
    ActorApply,
    ActorCriticQOptStates,
    ActorCriticQParams,
    AnakinExperimentOutput,
    CriticApply,
    LearnerFn,
    OnPolicyLearnerState,    
)
from stoix.evaluator import evaluator_setup, get_distribution_act_fn
from stoix.networks.base import FeedForwardActor as Actor
from stoix.networks.base import FeedForwardCritic as Critic
from stoix.networks.base import CompositeNetwork
from stoix.systems.ppo.ppo_types import PPOTransition
from stoix.utils import make_env as environments
from stoix.utils.checkpointing import Checkpointer
from stoix.utils.jax_utils import (
    merge_leading_dims,
    unreplicate_batch_dim,
    unreplicate_n_dims,
)
from stoix.utils.logger import LogEvent, StoixLogger
from stoix.utils.loss import clipped_value_loss, ppo_clip_loss
from stoix.utils.multistep import batch_truncated_generalized_advantage_estimation
from stoix.utils.total_timestep_checker import check_total_timesteps
from stoix.utils.training import make_learning_rate
from stoix.wrappers.episode_metrics import get_final_step_metrics


########################

from typing_extensions import NamedTuple
from stoix.base_types import Action, ActorCriticHiddenStates, Done, Truncated, Value

jax.config.update("jax_enable_x64", True)

alpha = 0.1

class PPOTransition(NamedTuple):
    """Transition tuple for PPO."""

    done: Done
    truncated: Truncated
    action: Action
    value: Value
    reward: chex.Array
    log_prob: chex.Array
    obs: chex.Array
    next_obs : chex.Array
    info: Dict

#############################


def get_learner_fn(
    env: Environment,
    apply_fns: Tuple[ActorApply, CriticApply],
    update_fns: Tuple[optax.TransformUpdateFn, optax.TransformUpdateFn],
    config: DictConfig,
) -> LearnerFn[OnPolicyLearnerState]:
    """Get the learner function."""

    # Get apply and update functions for actor and critic networks.
    actor_apply_fn, critic_apply_fn,q_apply_fn = apply_fns
    actor_update_fn, critic_update_fn,q_update_fn = update_fns

    def _update_step(
        learner_state: OnPolicyLearnerState, _: Any
    ) -> Tuple[OnPolicyLearnerState, Tuple]:
        """A single update of the network.

        This function steps the environment and records the trajectory batch for
        training. It then calculates advantages and targets based on the recorded
        trajectory and updates the actor and critic networks based on the calculated
        losses.

        Args:
            learner_state (NamedTuple):
                - params (ActorCriticParams): The current model parameters.
                - opt_states (OptStates): The current optimizer states.
                - key (PRNGKey): The random number generator state.
                - env_state (State): The environment state.
                - last_timestep (TimeStep): The last timestep in the current trajectory.
            _ (Any): The current metrics info.
        """

        def _env_step(
            learner_state: OnPolicyLearnerState, _: Any
        ) -> Tuple[OnPolicyLearnerState, PPOTransition]:
            """Step the environment."""
            params, opt_states, key, env_state, last_timestep = learner_state

            # SELECT ACTION
            key, policy_key = jax.random.split(key)
            actor_policy = actor_apply_fn(params.actor_params, last_timestep.observation)
            value = critic_apply_fn(params.critic_params, last_timestep.observation)
            action = actor_policy.sample(seed=policy_key)
            log_prob = actor_policy.log_prob(action)

            # STEP ENVIRONMENT
            env_state, timestep = jax.vmap(env.step, in_axes=(0, 0))(env_state, action)

            # LOG EPISODE METRICS
            done = (timestep.discount == 0.0).reshape(-1)
            truncated = (timestep.last() & (timestep.discount != 0.0)).reshape(-1)
            info = timestep.extras["episode_metrics"]

            transition = PPOTransition(
                done,
                truncated,
                action,
                value,
                timestep.reward,
                log_prob,
                last_timestep.observation,
                timestep.observation,
                info,
            )
            learner_state = OnPolicyLearnerState(params, opt_states, key, env_state, timestep)
            return learner_state, transition

        # STEP ENVIRONMENT FOR ROLLOUT LENGTH
        learner_state, traj_batch = jax.lax.scan(
            _env_step, learner_state, None, config.system.rollout_length
        )

        # CALCULATE ADVANTAGE
        params, opt_states, key, env_state, last_timestep = learner_state



      

   
     
        def _update_epoch(update_state: Tuple, _: Any) -> Tuple:


            def sample(input,key):

                size = config.system.rollout_length * config.arch.num_envs

                idx = jax.random.randint(key,(512,),minval=0,maxval=size)

                return jax.tree.map(lambda x: x[idx],input)



            

            """Update the network for a single epoch."""

            def _update_critics(train_state: Tuple, batch_info: Tuple) -> Tuple:
                """Update the network for a single minibatch."""

                # UNPACK TRAIN STATE AND BATCH INFO
                params, opt_states, key = train_state
                batch = batch_info



                def _critic_loss_fn(

                    critic_params: FrozenDict,
                    actor_params : FrozenDict,
                    batch: PPOTransition,
                    rng_key,
                ) -> Tuple:
                    """Calculate the critic loss."""
                    # RERUN NETWORK

                   

                    value = critic_apply_fn(critic_params, batch.obs)

             
                    policy = actor_apply_fn(actor_params,batch.obs)
                    entropy = policy.entropy(seed=rng_key)
                    targets = batch.reward +  config.system.gamma *(1.0 - batch.done) * (critic_apply_fn(critic_params,batch.next_obs)+alpha*entropy)
                    targets = jax.lax.stop_gradient(targets)
                    value_loss = 0.5*jnp.square(value-targets).mean()

                    critic_total_loss = config.system.vf_coef * value_loss
                    loss_info = {
                        "value_loss": value_loss,
                    }
                    return critic_total_loss, loss_info


                def _q_loss_fn(
                    q_params: FrozenDict,
                    actor_params: FrozenDict,
                    batch: PPOTransition,
                    rng_key: chex.PRNGKey,
                ) -> jnp.ndarray:
                    

                    batch = sample(batch,rng_key)

                    q_old_action = q_apply_fn(q_params, batch.obs, batch.action)
                    
                    next_dist  = actor_apply_fn(actor_params, batch.next_obs)
                    next_action = next_dist.sample(seed=rng_key)
                    next_log_p = next_dist.log_prob(next_action)
                    next_q = q_apply_fn(q_params, batch.next_obs, next_action)
                    
                    target_q = batch.reward + config.system.gamma *(1.0 - batch.done) *  (next_q- alpha*next_log_p)
                    q_error = q_old_action-jax.lax.stop_gradient(target_q)
                    q_loss = 0.5*jnp.square(q_error).mean()


                    loss_info = {
                        "q_loss": jnp.mean(q_loss),
                        "q_error": jnp.mean(jnp.abs(q_error)),
                        "q1_pred": jnp.mean(next_q),
                    }
                    return q_loss, loss_info
                
                
                key, actor_loss_key,critic_loss_key,q_loss_key = jax.random.split(key,4)
            
                
                # CALCULATE CRITIC LOSS
                critic_grad_fn = jax.grad(_critic_loss_fn, has_aux=True)
                critic_grads, critic_loss_info = critic_grad_fn(
                    params.critic_params,params.actor_params, batch,critic_loss_key,
                )
                
                
                # CALCULATE q LOSS
                q_grad_fn = jax.grad(_q_loss_fn, has_aux=True)
                q_grads, q_loss_info = q_grad_fn(
                    params.q_params,params.actor_params,batch, q_loss_key)
                
             
                critic_grads, critic_loss_info = jax.lax.pmean(
                    (critic_grads, critic_loss_info), axis_name="batch"
                )
                # pmean over devices.
                critic_grads, critic_loss_info = jax.lax.pmean(
                    (critic_grads, critic_loss_info), axis_name="device"
                )
                
                
                q_grads, q_loss_info = jax.lax.pmean(
                    (q_grads, q_loss_info), axis_name="batch"
                )
                # pmean over devices.
                q_grads, q_loss_info = jax.lax.pmean(
                    (q_grads, q_loss_info), axis_name="device"
                )

             

                # UPDATE CRITIC PARAMS AND OPTIMISER STATE
                critic_updates, critic_new_opt_state = critic_update_fn(
                    critic_grads, opt_states.critic_opt_state
                )
                critic_new_params = optax.apply_updates(params.critic_params, critic_updates)
                
                   
                # UPDATE CRITIC PARAMS AND OPTIMISER STATE
                q_updates, q_new_opt_state = q_update_fn(
                    q_grads, opt_states.q_opt_state
                )
                q_new_params = optax.apply_updates(params.q_params, q_updates)

                

                # PACK NEW PARAMS AND OPTIMISER STATE
                new_params = ActorCriticQParams(params.actor_params, critic_new_params,q_new_params)
                new_opt_state = ActorCriticQOptStates(opt_states.actor_opt_state, critic_new_opt_state,q_new_opt_state)

                # PACK LOSS INFO
                loss_info = {
                    #**actor_loss_info,
                    **critic_loss_info,
                }
                return (new_params, new_opt_state, key), loss_info
            
            
            
            def _update_actor(train_state: Tuple, batch_info: Tuple) -> Tuple:
                """Update the network for a single minibatch."""

                # UNPACK TRAIN STATE AND BATCH INFO
                params, opt_states, key = train_state
                batch,advantages = batch_info

                
                def _actor_loss_fn(
                    actor_params: FrozenDict,
                    batch: PPOTransition,
                    gae: chex.Array,
                    rng_key: chex.PRNGKey,
                ) -> Tuple:
                    """Calculate the actor loss."""


                    batch = sample(batch,rng_key)
                    adv_sample = sample(gae,rng_key)

                    # RERUN NETWORK
                    actor_policy = actor_apply_fn(actor_params, batch.obs)
                    log_prob = actor_policy.log_prob(batch.action)

                    # CALCULATE ACTOR LOSS
                    loss_actor = ppo_clip_loss(
                        log_prob, batch.log_prob, adv_sample, config.system.clip_eps
                    )
                    entropy = actor_policy.entropy(seed=rng_key).mean()

                    #total_loss_actor = loss_actor - config.system.ent_coef * entropy
                    total_loss_actor = loss_actor
                    loss_info = {
                        "actor_loss": loss_actor,
                        "entropy": entropy,
                    }

                    return total_loss_actor, loss_info
                
                
                # CALCULATE ACTOR LOSS
                key, actor_loss_key,critic_loss_key,q_loss_key = jax.random.split(key,4)
                actor_grad_fn = jax.grad(_actor_loss_fn, has_aux=True)
                actor_grads, actor_loss_info = actor_grad_fn(
                    params.actor_params,
                    batch,
                    advantages,
                    actor_loss_key,
                )
                
                
                # Compute the parallel mean (pmean) over the batch.
                # This calculation is inspired by the Anakin architecture demo notebook.
                # available at https://tinyurl.com/26tdzs5x
                # This pmean could be a regular mean as the batch axis is on the same device.
                actor_grads, actor_loss_info = jax.lax.pmean(
                    (actor_grads, actor_loss_info), axis_name="batch"
                )
                # pmean over devices.
                actor_grads, actor_loss_info = jax.lax.pmean(
                    (actor_grads, actor_loss_info), axis_name="device"
                )
                
                
                
                   # UPDATE ACTOR PARAMS AND OPTIMISER STATE
                actor_updates, actor_new_opt_state = actor_update_fn(
                    actor_grads, opt_states.actor_opt_state
                )
                actor_new_params = optax.apply_updates(params.actor_params, actor_updates)
                
                
                
                # PACK NEW PARAMS AND OPTIMISER STATE
                new_params = params._replace(actor_params=actor_new_params)
                new_opt_state = opt_states._replace(actor_opt_state=actor_new_opt_state)

                # PACK LOSS INFO
                loss_info = {
                    **actor_loss_info,
                    #**critic_loss_info,
                }
                return (new_params, new_opt_state, key), loss_info


                
            params, opt_states,traj_batch,key = update_state
            key, shuffle_key = jax.random.split(key)

            # SHUFFLE MINIBATCHES
            
           

            minibatches = jax.vmap(sample,in_axes=(None,0))(traj_batch,jax.random.split(shuffle_key,128))

            batch = minibatches[0]
            #jax.debug.print(f' hoaaaaaa {jax.tree.map(lambda x: x.shape, batch)}')


           

            # UPDATE CRITICS
            (params, opt_states, key), loss_info = jax.lax.scan(
                _update_critics, (params, opt_states, key),minibatches,
            )
            
            
            v = critic_apply_fn(params.critic_params, traj_batch.obs)
            q = q_apply_fn(params.q_params,traj_batch.obs,traj_batch.action)
            policy = actor_apply_fn(params.actor_params,traj_batch.obs)
            entropy = policy.entropy(seed=key)
            advantages = q-v + alpha*(-traj_batch.log_prob -entropy )
            advantages = jax.lax.stop_gradient(advantages)

            minibatches = jax.vmap(sample,in_axes=(None,0))((traj_batch,advantages),jax.random.split(shuffle_key,32))
                        
            # UPDATE ACTOR
            (params, opt_states, key), loss_info = jax.lax.scan(
                _update_actor, (params, opt_states, key),minibatches)

            update_state = (params, opt_states,traj_batch,key)
            return update_state, loss_info


        def merge_first_two_dimensions(arr):
            # Reshape the array to merge the first two dimensions
            return arr.reshape((-1, *arr.shape[2:])) if arr.ndim >=3 else arr.reshape(-1)
        
        traj_batch = jax.tree_util.tree_map(merge_first_two_dimensions, traj_batch) ### TODO: maybe do without the if
        
        update_state = (params, opt_states,traj_batch,key)

        # UPDATE EPOCHS
        update_state, loss_info = jax.lax.scan(
            _update_epoch, update_state, None, config.system.epochs
        )

        params, opt_states,traj_batch,key = update_state
        learner_state = OnPolicyLearnerState(params, opt_states, key, env_state, last_timestep)
        metric = traj_batch.info
        return learner_state, (metric, loss_info)

    def learner_fn(
        learner_state: OnPolicyLearnerState,
    ) -> AnakinExperimentOutput[OnPolicyLearnerState]:
        """Learner function.

        This function represents the learner, it updates the network parameters
        by iteratively applying the `_update_step` function for a fixed number of
        updates. The `_update_step` function is vectorized over a batch of inputs.

        Args:
            learner_state (NamedTuple):
                - params (ActorCriticParams): The initial model parameters.
                - opt_states (OptStates): The initial optimizer state.
                - key (chex.PRNGKey): The random number generator state.
                - env_state (LogEnvState): The environment state.
                - timesteps (TimeStep): The initial timestep in the initial trajectory.
        """

        batched_update_step = jax.vmap(_update_step, in_axes=(0, None), axis_name="batch")

        learner_state, (episode_info, loss_info) = jax.lax.scan(
            batched_update_step, learner_state, None, config.arch.num_updates_per_eval
        )
        return AnakinExperimentOutput(
            learner_state=learner_state,
            episode_metrics=episode_info,
            train_metrics=loss_info,
        )

    return learner_fn


def learner_setup(
    env: Environment, keys: chex.Array, config: DictConfig
) -> Tuple[LearnerFn[OnPolicyLearnerState], Actor, OnPolicyLearnerState]:
    """Initialise learner_fn, network, optimiser, environment and states."""
    # Get available TPU cores.
    n_devices = len(jax.devices())

    # Get number of actions.
    action_dim = int(env.action_spec().shape[-1])
    config.system.action_dim = action_dim
    config.system.action_minimum = float(env.action_spec().minimum)
    config.system.action_maximum = float(env.action_spec().maximum)

    num_actions = int(env.action_spec().shape[-1])
    config.system.action_dim = num_actions
    config.system.action_minimum = float(env.action_spec().minimum)
    config.system.action_maximum = float(env.action_spec().maximum)

    # PRNG keys.
    key, actor_net_key, critic_net_key = keys

    # Define network and optimiser.
    actor_torso = hydra.utils.instantiate(config.network.actor_network.pre_torso)
    actor_action_head = hydra.utils.instantiate(
        config.network.actor_network.action_head,
        action_dim=num_actions,
        minimum=config.system.action_minimum,
        maximum=config.system.action_maximum,
    )
    critic_torso = hydra.utils.instantiate(config.network.critic_network.pre_torso)
    critic_head = hydra.utils.instantiate(config.network.critic_network.critic_head)
    
    
    def create_q_network(cfg: DictConfig) -> CompositeNetwork:
        q_network_input = hydra.utils.instantiate(cfg.network.q_network.input_layer)
        q_network_torso = hydra.utils.instantiate(cfg.network.q_network.pre_torso)
        q_network_head = hydra.utils.instantiate(cfg.network.q_network.critic_head)
        return CompositeNetwork([q_network_input, q_network_torso, q_network_head])
    
    
    actor_network = Actor(torso=actor_torso, action_head=actor_action_head)
    critic_network = Critic(torso=critic_torso, critic_head=critic_head)
    q_network = create_q_network(config)
    
    actor_lr = make_learning_rate(
        config.system.actor_lr, config, config.system.epochs, config.system.num_minibatches
    )
    critic_lr = make_learning_rate(
        config.system.critic_lr, config, config.system.epochs, config.system.num_minibatches
    )
 
    actor_optim = optax.chain(
        optax.clip_by_global_norm(config.system.max_grad_norm),
        optax.adam(actor_lr, eps=1e-5),
    )
    critic_optim = optax.chain(
        optax.clip_by_global_norm(config.system.max_grad_norm),
        optax.adam(critic_lr, eps=1e-5),
    )
    
    q_optim = optax.chain(
        optax.clip_by_global_norm(config.system.max_grad_norm),
        optax.adam(critic_lr, eps=1e-5),
    )


    # Initialise observation
    init_x = env.observation_spec().generate_value()
    init_x = jax.tree_util.tree_map(lambda x: x[None, ...], init_x)
    init_a = jnp.zeros((1, action_dim))

    # Initialise actor params and optimiser state.
    actor_params = actor_network.init(actor_net_key, init_x)
    actor_opt_state = actor_optim.init(actor_params)

    # Initialise critic params and optimiser state.
    critic_params = critic_network.init(critic_net_key, init_x)
    critic_opt_state = critic_optim.init(critic_params)

    # Initialise critic params and optimiser state.
    q_params = q_network.init(critic_net_key, init_x,init_a)
    q_opt_state = q_optim.init(q_params)

    
    # Pack params.
    params = ActorCriticQParams(actor_params, critic_params,q_params)

    actor_network_apply_fn = actor_network.apply
    critic_network_apply_fn = critic_network.apply
    q_network_apply_fn = q_network.apply

    # Pack apply and update functions.
    apply_fns = (actor_network_apply_fn, critic_network_apply_fn,q_network_apply_fn)
    update_fns = (actor_optim.update, critic_optim.update,q_optim.update)

    # Get batched iterated update and replicate it to pmap it over cores.
    learn = get_learner_fn(env, apply_fns, update_fns, config)
    learn = jax.pmap(learn, axis_name="device")

    # Initialise environment states and timesteps: across devices and batches.
    key, *env_keys = jax.random.split(
        key, n_devices * config.arch.update_batch_size * config.arch.num_envs + 1
    )
    env_states, timesteps = jax.vmap(env.reset, in_axes=(0))(
        jnp.stack(env_keys),
    )
    reshape_states = lambda x: x.reshape(
        (n_devices, config.arch.update_batch_size, config.arch.num_envs) + x.shape[1:]
    )
    # (devices, update batch size, num_envs, ...)
    env_states = jax.tree_util.tree_map(reshape_states, env_states)
    timesteps = jax.tree_util.tree_map(reshape_states, timesteps)

    # Load model from checkpoint if specified.
    if config.logger.checkpointing.load_model:
        loaded_checkpoint = Checkpointer(
            model_name=config.system.system_name,
            **config.logger.checkpointing.load_args,  # Other checkpoint args
        )
        # Restore the learner state from the checkpoint
        restored_params, _ = loaded_checkpoint.restore_params()
        # Update the params
        params = restored_params

    # Define params to be replicated across devices and batches.
    key, step_key = jax.random.split(key)
    step_keys = jax.random.split(step_key, n_devices * config.arch.update_batch_size)
    reshape_keys = lambda x: x.reshape((n_devices, config.arch.update_batch_size) + x.shape[1:])
    step_keys = reshape_keys(jnp.stack(step_keys))
    opt_states = ActorCriticQOptStates(actor_opt_state, critic_opt_state,q_opt_state)
    replicate_learner = (params, opt_states,)

    # Duplicate learner for update_batch_size.
    broadcast = lambda x: jnp.broadcast_to(x, (config.arch.update_batch_size,) + x.shape)
    replicate_learner = jax.tree_util.tree_map(broadcast, replicate_learner)

    # Duplicate learner across devices.
    replicate_learner = flax.jax_utils.replicate(replicate_learner, devices=jax.devices())

    # Initialise learner state.
    params, opt_states = replicate_learner
    init_learner_state = OnPolicyLearnerState(params, opt_states, step_keys, env_states, timesteps)

    return learn, actor_network, init_learner_state


def run_experiment(_config: DictConfig) -> float:
    """Runs experiment."""
    config = copy.deepcopy(_config)

    # Calculate total timesteps.
    n_devices = len(jax.devices())
    config.num_devices = n_devices
    config = check_total_timesteps(config)
    assert (
        config.arch.num_updates >= config.arch.num_evaluation
    ), "Number of updates per evaluation must be less than total number of updates."

    # Create the environments for train and eval.
    env, eval_env = environments.make(config=config)

    # PRNG keys.
    key, key_e, actor_net_key, critic_net_key = jax.random.split(
        jax.random.PRNGKey(config.arch.seed), num=4
    )

    # Setup learner.
    learn, actor_network, learner_state = learner_setup(
        env, (key, actor_net_key, critic_net_key), config
    )

    # Setup evaluator.
    evaluator, absolute_metric_evaluator, (trained_params, eval_keys) = evaluator_setup(
        eval_env=eval_env,
        key_e=key_e,
        eval_act_fn=get_distribution_act_fn(config, actor_network.apply),
        params=learner_state.params.actor_params,
        config=config,
    )

    # Calculate number of updates per evaluation.
    config.arch.num_updates_per_eval = config.arch.num_updates // config.arch.num_evaluation
    steps_per_rollout = (
        n_devices
        * config.arch.num_updates_per_eval
        * config.system.rollout_length
        * config.arch.update_batch_size
        * config.arch.num_envs
    )

    # Logger setup
    logger = StoixLogger(config)
    cfg: Dict = OmegaConf.to_container(config, resolve=True)
    cfg["arch"]["devices"] = jax.devices()
    pprint(cfg)

    # Set up checkpointer
    save_checkpoint = config.logger.checkpointing.save_model
    if save_checkpoint:
        checkpointer = Checkpointer(
            metadata=config,  # Save all config as metadata in the checkpoint
            model_name=config.system.system_name,
            **config.logger.checkpointing.save_args,  # Checkpoint args
        )

    # Run experiment for a total number of evaluations.
    max_episode_return = jnp.float32(-1e7)
    best_params = unreplicate_batch_dim(learner_state.params.actor_params)
    for eval_step in range(config.arch.num_evaluation):
        # Train.
        start_time = time.time()

        learner_output = learn(learner_state)
        jax.block_until_ready(learner_output)

        # Log the results of the training.
        elapsed_time = time.time() - start_time
        t = int(steps_per_rollout * (eval_step + 1))
        episode_metrics, ep_completed = get_final_step_metrics(learner_output.episode_metrics)
        episode_metrics["steps_per_second"] = steps_per_rollout / elapsed_time

        # Separately log timesteps, actoring metrics and training metrics.
        logger.log({"timestep": t}, t, eval_step, LogEvent.MISC)
        if ep_completed:  # only log episode metrics if an episode was completed in the rollout.
            logger.log(episode_metrics, t, eval_step, LogEvent.ACT)
        train_metrics = learner_output.train_metrics
        # Calculate the number of optimiser steps per second. Since gradients are aggregated
        # across the device and batch axis, we don't consider updates per device/batch as part of
        # the SPS for the learner.
        opt_steps_per_eval = config.arch.num_updates_per_eval * (
            config.system.epochs * config.system.num_minibatches
        )
        train_metrics["steps_per_second"] = opt_steps_per_eval / elapsed_time
        logger.log(train_metrics, t, eval_step, LogEvent.TRAIN)

        # Prepare for evaluation.
        start_time = time.time()
        trained_params = unreplicate_batch_dim(
            learner_output.learner_state.params.actor_params
        )  # Select only actor params
        key_e, *eval_keys = jax.random.split(key_e, n_devices + 1)
        eval_keys = jnp.stack(eval_keys)
        eval_keys = eval_keys.reshape(n_devices, -1)

        # Evaluate.
        evaluator_output = evaluator(trained_params, eval_keys)
        jax.block_until_ready(evaluator_output)

        # Log the results of the evaluation.
        elapsed_time = time.time() - start_time
        episode_return = jnp.mean(evaluator_output.episode_metrics["episode_return"])

        steps_per_eval = int(jnp.sum(evaluator_output.episode_metrics["episode_length"]))
        evaluator_output.episode_metrics["steps_per_second"] = steps_per_eval / elapsed_time
        logger.log(evaluator_output.episode_metrics, t, eval_step, LogEvent.EVAL)

        if save_checkpoint:
            # Save checkpoint of learner state
            checkpointer.save(
                timestep=int(steps_per_rollout * (eval_step + 1)),
                unreplicated_learner_state=unreplicate_n_dims(learner_output.learner_state),
                episode_return=episode_return,
            )

        if config.arch.absolute_metric and max_episode_return <= episode_return:
            best_params = copy.deepcopy(trained_params)
            max_episode_return = episode_return

        # Update runner state to continue training.
        learner_state = learner_output.learner_state

    # Measure absolute metric.
    if config.arch.absolute_metric:
        start_time = time.time()

        key_e, *eval_keys = jax.random.split(key_e, n_devices + 1)
        eval_keys = jnp.stack(eval_keys)
        eval_keys = eval_keys.reshape(n_devices, -1)

        evaluator_output = absolute_metric_evaluator(best_params, eval_keys)
        jax.block_until_ready(evaluator_output)

        elapsed_time = time.time() - start_time
        t = int(steps_per_rollout * (eval_step + 1))
        steps_per_eval = int(jnp.sum(evaluator_output.episode_metrics["episode_length"]))
        evaluator_output.episode_metrics["steps_per_second"] = steps_per_eval / elapsed_time
        logger.log(evaluator_output.episode_metrics, t, eval_step, LogEvent.ABSOLUTE)

    # Stop the logger.
    logger.stop()
    # Record the performance for the final evaluation run. If the absolute metric is not
    # calculated, this will be the final evaluation run.
    eval_performance = float(jnp.mean(evaluator_output.episode_metrics[config.env.eval_metric]))
    return eval_performance


@hydra.main(
    config_path="../../../configs/default/anakin",
    config_name="default_ff_ppo_continuous.yaml",
    version_base="1.2",
)
def hydra_entry_point(cfg: DictConfig) -> float:
    """Experiment entry point."""
    # Allow dynamic attributes.
    OmegaConf.set_struct(cfg, False)

    # Run experiment.
    eval_performance = run_experiment(cfg)
    print(f"{Fore.CYAN}{Style.BRIGHT}PPO experiment completed{Style.RESET_ALL}")
    return eval_performance


if __name__ == "__main__":
    hydra_entry_point()