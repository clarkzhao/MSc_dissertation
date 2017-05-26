from agent import Agent
from utils.constant import PlayerAction


class HumanAgent(Agent):
    """The agent controller by human"""

    def __init__(self):
        super(HumanAgent, self).__init__()
        self.is_human = True
        self.is_prey = False
        pass

    def begin_episode(self):
        super(HumanAgent, self).begin_episode()
        pass

    def act(self):
        return PlayerAction.STAND_STILL

    def __str__(self):
        return "human agent"
