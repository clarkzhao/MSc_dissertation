from agent import Agent
from .model import *
from utils.constant import *
import torch
from torch.autograd import Variable
import torch.optim as optim
import random
import numpy as np


class BaseDQNAgent(Agent):
    def __init__(self):
        super(BaseDQNAgent, self).__init__()
        self.is_DQN = True
        # game environment

        # epsilon (exploration rate) anealing
        self.eps_start = DQNSetting.EPS_START
        self.eps = self.eps_start
        self.eps_end = DQNSetting.EPS_END
        self.eps_len = DQNSetting.EPS_DECAY_LEN
        self.eps_eval = DQNSetting.EPS_EVAL

        # Learning rate
        self.lr = DQNSetting.LR
        self.learning_start = DQNSetting.LEARNING_START_IN_EPISODE
        self.clip_grad = DQNSetting.CLIP_GRAD


        # Q-learning parameters
        self.gamma = DQNSetting.GAMMA  #discounted factor

        self.n_hist = DQNSetting.N_HIST
        self.n_cols = DQNSetting.N_COLS
        self.input_dims = [self.n_cols*self.n_hist,
                           GameSetting.AGENT_VIEW_RANGE[0],
                           GameSetting.AGENT_VIEW_RANGE[1]]

        self.use_cuda = torch.cuda.is_available()
        self.dtype = torch.cuda.FloatTensor if torch.cuda.is_available() else torch.FloatTensor
        self.last_idx = None

        # q-networks
        n_input_features = int(np.prod(self.input_dims))
        self.q_network = DQN(n_input_features, self.n_actions).type(self.dtype)
        # target_q_network
        self.target_q = DQN(n_input_features, self.n_actions).type(self.dtype)
        # self.logger.warning(self.q_network.__repr__)
        self.recent_action = None
        self.optim = optim.RMSprop
        # self.optim = optim.Adam
        # self.optim = optim.SGD
        self.optimizer = None
        self.training = True
        self.target_update_fre = DQNSetting.TARGET_UPDATE_FRE

        # replay buffer
        self.buffer_size = DQNSetting.MEMORY_SIZE  # size of memory buffer

        # log agent information
        self.best_avg_reward = None
        self.v_avg_log = []
        self.tderr_avg_log = []
        self.reward_log = []
        self.action_log = []
        self.reward_avg_log = []

    def begin_episode(self):
        super(BaseDQNAgent, self).begin_episode()

    def epsilon_greedy_policy(self, model, state, step):
        """
        Perform epsilon greedy policy
        :param model: a DQN model
        :param state: current state
        :param step: number of step in an episode
        :return: action from the policy
        """
        # Construct an epsilon greedy policy
        # linearly decay of the exploration rate, epsilon
        if self.training:
            self.eps = self.eps_end + max(0., (self.eps_start - self.eps_end) *
                                          (self.eps_len - max(0., self.step - self.learning_start)) / self.eps_len)
        else:
            self.eps = self.eps_eval

        # sample randomly and compare to the epsilon
        sample = random.random()
        if sample > self.eps:
            s = torch.from_numpy(state).type(self.dtype).unsqueeze(0) / 255.0
            # Use volatile = True if variable is only used in inference mode, i.e. don’t save the history
            q_vals = model(Variable(s, volatile=True)).data  # all output action values
            return np.argmax(q_vals.numpy())  # return the action with greedy policy that has largest action value)
        else:
            return random.randrange(self.n_actions)

    def _sample_validation_data(self):
        self.valid_data = self.memory_buffer.sample(DQNSetting.VALID_SIZE)

    def compute_validation_stats(self):
        raise NotImplementedError

    def get_Q_update(self, state, action, reward, next_state, term, eff_lr):
        raise NotImplementedError

    def act(self, observation):
        raise NotImplementedError

    def learn(self, reward, terminal):
        raise NotImplementedError

    def load_model(self, model_file):
        if model_file:
            self.q_network.load_state_dict(torch.load(model_file))
            return True
        else:
            return False


class DQNAgent(BaseDQNAgent):
    """The agent controller by human"""

    def __init__(self):
        super(DQNAgent, self).__init__()
        self.memory_buffer = ReplayBuffer(size=self.buffer_size)  # where the transitions are stored

    def get_Q_update(self, state, action, reward, next_state, term, eff_lr):
        """
        :param
        state: np.array, current state
        next_state: np.array, next state after the action
        action: int, the action taken for the agent
        reward: float, the reward received from the environment
        term: Boolean, whether or not the episode is ending.
        :return:
        temporal difference error given a transitions
        """
        # convert numpy to Torch.Variable
        cur_s = Variable(torch.from_numpy(state).type(self.dtype) / 255.0)  # normalize the state
        next_s = Variable(torch.from_numpy(next_state).type(self.dtype) / 255.0)
        action = Variable(torch.from_numpy(action).long())
        reward = Variable(torch.from_numpy(reward).type(self.dtype))
        not_term = Variable(torch.from_numpy(1 - term)).type(self.dtype)

        if self.use_cuda:
            action = action.cuda()

        if DQNSetting.DOUBLE:
            q_values = self.q_network(cur_s)
            _, q2_max_actions = q_values.max(dim=1)
            q2_max = self.target_q(next_s)
            q2_max = q2_max.gather(1, q2_max_actions)

        # Compute max_a Q(s2, a)
        # Detach this variable from the current graph since we don't want gradients to propagate
        else:
            q2_max = self.target_q(next_s).detach().max(1)[0]
        # Compute q2 = (1-terminal) * gamma * max_a Q(s2, a)
        q2 = not_term * q2_max
        # Compute target = r + (1-terminal) * gamma * max_a Q(s2, a)
        target_q = reward + (q2 * self.gamma)
        # Compute current Q values Q(s,a)
        q = self.q_network(cur_s).gather(1, action.unsqueeze(1))

        # delta = target_q - q
        # # clip the TD error between [-1 , 1]
        # delta_clipped = delta.clamp(-1, 1)

        # Compute TD error  delta = Huber loss( Q(s, a), r + (1-terminal) * gamma * max_a Q(s2, a) )
        delta = F.smooth_l1_loss(q, target_q)
        # delta = 1. / target_q.numel() * sum((a - b).abs().sum() for a, b in zip(target_q, q))

        # delta = target_q - q

        if not self.training:   # then is being called from _compute_validation_stats, which is just doing inference
            delta = Variable(delta.data)  # detach it from the graph
        return q2.data.clone().mean(), delta

    def act(self, observation):
        """
        Select an action according to the observation from the agent
        :param observation: the np.array with the shape of
                            (img_h, img_w, img_c), and data type of np.uint8
        :return: action with python built-in numeric type of int
        """
        # Store lastest observation in replay memory and last_idx can be used to store action, reward, done
        self.last_idx = self.memory_buffer.store_frame(observation)
        # encode_recent_observation will take the latest observation
        # that you pushed into the buffer and compute the corresponding
        # input that should be given to a Q network by appending some
        # previous frames.
        state = self.memory_buffer.encode_recent_observation()
        if self.step > self.learning_start:
            action = self.epsilon_greedy_policy(self.q_network, state, self.step)
        # If the learning haven't starts, choose random action
        else:
            action = random.randrange(self.n_actions)
        # Book keeping
        self.recent_action = action
        return action

    def compute_validation_stats(self):
        state, action, reward, next_state, term = self.valid_data
        return self.get_Q_update(state, action, reward, next_state, term, None)

    def learn(self, reward, terminal):
        """
        Update the parameter for the q-network
        :param reward: the reward obtained
        :param terminal: whether or not the episode is ended
        :return:
        """
        self.memory_buffer.store_effect(self.last_idx, self.recent_action, reward, terminal)
        if not self.training:
            # We're done here. No need to update the replay memory since we only use the
            # recent memory to obtain the state over the most recent observations.
            return

        # sample validation data right before training started
        # NOTE: here validation data is not entirely clean since the agent might see those data during training
        # NOTE: but it's ok as is also the case in the original dqn code, cos those data are not used to judge performance like in supervised learning
        # NOTE: but rather to inspect the whole learning procedure; of course we can separate those entirely from the training data but it's not worth the effort
        if self.step == self.learning_start + 1:
            self._sample_validation_data()

        if self.step > self.learning_start and self.memory_buffer.can_sample(DQNSetting.BATCH_SIZE):
            state, action, reward, next_state, term = self.memory_buffer.sample(DQNSetting.BATCH_SIZE)
            _, delta = self.get_Q_update(state, action, reward, next_state, term, None)
            self.optimizer.zero_grad()
            # run backward pass and clip gradient
            delta.backward()
            for param in self.q_network.parameters():
                param.grad.data.clamp_(-self.clip_grad, self.clip_grad)
            # for param in self.q_network.parameters():
            #     param.grad.data.clamp_(-1, 40)
            # Perform the update
            self.optimizer.step()
            # Update the target network
            if self.step % self.target_update_fre == 0:
                self.target_q.load_state_dict(self.q_network.state_dict())
        return


class DRUQNAgent(BaseDQNAgent):
    def __init__(self):
        super(DRUQNAgent, self).__init__()
        self.is_DRUQN = True
        self.memory_buffer = ReplayBufferRU(size=self.buffer_size)  # where the transitions are stored
        self.eff_lr = None
        self.alpha = DQNSetting.ALPHA

    def get_Q_update(self, state, action, reward, next_state, term, eff_lr):
        """
        :param
        state: np.array current state
        next_state: np.array, next state after the action
        action: int, the action taken for the agent
        reward: float, the reward received from the environment
        term: Boolean, whether or not the episode is ending.
        eff_lr: float
        :return:
        temporal difference error given a transitions
        """
        # convert numpy to Torch.Variable
        cur_s = Variable(torch.from_numpy(state).type(self.dtype) / 255.0)  # normalize the state
        next_s = Variable(torch.from_numpy(next_state).type(self.dtype) / 255.0)
        action = Variable(torch.from_numpy(action).long())
        reward = Variable(torch.from_numpy(reward).type(self.dtype))
        not_term = Variable(torch.from_numpy(1 - term)).type(self.dtype)
        # eff_lr = Variable(torch.from_numpy(eff_lr).type(self.dtype))

        if self.use_cuda:
            action = action.cuda()

        if DQNSetting.DOUBLE:
            q_values = self.q_network(cur_s)
            _, q2_max_actions = q_values.max(dim=1)
            q2_max = self.target_q(next_s)
            q2_max = q2_max.gather(1, q2_max_actions)

        # Compute max_a Q(s2, a)
        # Detach this variable from the current graph since we don't want gradients to propagate
        else:
            # Compute max_a Q(s2, a)
            # Detach this variable from the current graph since we don't want gradients to propagate
            q2_max = self.target_q(next_s).detach().max(1)[0]
        # Compute q2 = (1-terminal) * gamma * max_a Q(s2, a)
        q2 = not_term * q2_max
        # Compute target = r + (1-terminal) * gamma * max_a Q(s2, a)
        target_q = reward + (q2 * self.gamma)
        # Compute current Q values Q(s,a)
        q = self.q_network(cur_s).gather(1, action.unsqueeze(1))

        # delta = target_q - q
        # # clip the TD error between [-1 , 1]
        # delta_clipped = delta.clamp(-1, 1)
        # Compute TD error  delta = Huber loss( Q(s, a), r + (1-terminal) * gamma * max_a Q(s2, a) )
        delta = F.smooth_l1_loss(q, target_q)
        # delta = target_q - q
        # L1 loss
        # delta = 1. / target_q.numel() * sum((c*(a - b)).abs().sum() for a, b, c in zip(target_q, q, eff_lr))

        if not self.training:   # then is being called from _compute_validation_stats, which is just doing inference
            delta = Variable(delta.data)  # detach it from the graph
        return q2.data.clone().mean(), delta

    def act(self, observation):
        """
        Select an action according to the observation from the agent
        :param observation: the np.array with the shape of
                            (img_h, img_w, img_c), and data type of np.uint8
        :return: action with python built-in numeric type of int
        """
        # Store lastest observation in replay memory and last_idx can be used to store action, reward, done
        self.last_idx = self.memory_buffer.store_frame(observation)
        # encode_recent_observation will take the latest observation
        # that you pushed into the buffer and compute the corresponding
        # input that should be given to a Q network by appending some
        # previous frames.
        state = self.memory_buffer.encode_recent_observation()
        if self.step > self.learning_start:
            action = self.epsilon_greedy_policy(self.q_network, state, self.step)
        # If the learning haven't starts, choose random action
        else:
            action = random.randrange(self.n_actions)

        q_vals = self.q_network(Variable(torch.from_numpy(state).type(self.dtype).unsqueeze(0) / 255.0
                                         , volatile=True)).data  # all output action values
        greedy_action = np.argmax(q_vals.numpy())
        if action == greedy_action:
            p = 1.0 - (float(self.n_actions) - 1.0) / float(self.n_actions) * self.eps
        else:
            p = self.eps / float(self.n_actions)
        self.eff_lr = 1 - (1 - self.alpha) ** (1/p)
        # Book keeping
        self.recent_action = action

        return action

    def update_eff_lr(self, action, state):
        q_vals = self.q_network(Variable(torch.from_numpy(state).type(self.dtype).unsqueeze(0) / 255.0
                                         , volatile=True)).data  # all output action values
        greedy_action = np.argmax(q_vals.numpy())
        if action == greedy_action:
            p = 1.0 - (float(self.n_actions) - 1.0) / float(self.n_actions) * self.eps
        else:
            p = self.eps / float(self.n_actions)
        eff_lr = 1 - (1 - self.alpha) ** (1/p)
        return eff_lr

    def learn(self, reward, terminal):
        """
        Update the parameter for the q-network
        :param reward: the reward obtained
        :param terminal: whether or not the episode is ended
        :param eff_lr: effective learning rate
        :return:
        """
        self.memory_buffer.store_effect(self.last_idx, self.recent_action, reward, terminal, self.eff_lr)
        if not self.training:
            # We're done here. No need to update the replay memory since we only use the
            # recent memory to obtain the state over the most recent observations.
            return

        # sample validation data right before training started
        # NOTE: here validation data is not entirely clean since the agent might see those data during training
        # NOTE: but it's ok as is also the case in the original dqn code, cos those data are not used to judge performance like in supervised learning
        # NOTE: but rather to inspect the whole learning procedure; of course we can separate those entirely from the training data but it's not worth the effort
        if self.step == self.learning_start + 1:
            self._sample_validation_data()

        if self.step > self.learning_start and self.memory_buffer.can_sample(DQNSetting.BATCH_SIZE):
            state, action, reward, next_state, term, eff_lr = self.memory_buffer.sample(DQNSetting.BATCH_SIZE)
            for i in range(DQNSetting.BATCH_SIZE):
                # state, action, reward, next_state, term, eff_lr = self.memory_buffer.sample(DQNSetting.BATCH_SIZE)
                # _, delta = self.get_Q_update(state, action, reward, next_state, term, eff_lr)
                _, delta = self.get_Q_update(np.expand_dims(state[i], axis=0),
                                             action[i].reshape([1, ]),
                                             reward[i].reshape([1, ]),
                                             np.expand_dims(next_state[i], axis=0),
                                             term[i].reshape([1, ]),
                                             eff_lr[i].reshape([1, ]))
                self.optimizer.zero_grad()
                # self.q_network.zero_grad()
                # run backward pass and clip gradient
                delta.backward()
                # for param in self.q_network.parameters():
                    # param.grad.data.clamp_(-self.clip_grad, self.clip_grad)
                # Perform the update
                # update the effective learning rate
                # eff_lr[i] = self.update_eff_lr(action[i], state[i])
                for group in self.optimizer.param_groups:
                    group['lr'] = float(eff_lr[i]) * DQNSetting.LR_RU
                self.optimizer.step()
            # Update the target network
            if self.step % self.target_update_fre == 0:
                self.target_q.load_state_dict(self.q_network.state_dict())
        return

    def compute_validation_stats(self):
        state, action, reward, next_state, term, eff_lr = self.valid_data
        return self.get_Q_update(state, action, reward, next_state, term, eff_lr)
