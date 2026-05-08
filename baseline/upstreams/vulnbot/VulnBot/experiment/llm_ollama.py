import dataclasses
import inspect
import time
import json

import httpx
import loguru
import requests
from typing import List, Dict
from uuid import uuid1

from openai import OpenAI
from tenacity import retry, stop_after_attempt

logger = loguru.logger
from ollama import Client

@dataclasses.dataclass
class Message:
    ask_id: str = None
    ask: dict = None
    answer: dict = None
    answer_id: str = None
    request_start_timestamp: float = None
    request_end_timestamp: float = None
    time_escaped: float = None


@dataclasses.dataclass
class Conversation:
    conversation_id: str = None
    message_list: List[Message] = dataclasses.field(default_factory=list)

    def __hash__(self):
        return hash(self.conversation_id)

    def __eq__(self, other):
        if not isinstance(other, Conversation):
            return False
        return self.conversation_id == other.conversation_id


class OLLAMAPI:
    def __init__(self):
        self.OLLAMA_API_URL = ""
        self.conversation_dict: Dict[str, Conversation] = {}
        self.history_length = 5  # Max number of messages to retain in conversation history
        self.error_waiting_time = 3  # Retry waiting time in seconds

    def _chat_completion(self, history: List[dict]) -> str:
        try:
            client = Client(host=self.OLLAMA_API_URL)
            options = {
                "temperature": 0.5
            }
            response = client.chat(
                model="",
                messages=history,
                options=options,
            )
            ans = response["message"]["content"]
            return ans
        except Exception as e:
            logger.error(e)

    def send_new_message(self, message: str, image_url: str = None):
        # create a message
        start_time = time.time()
        if image_url is not None and type(image_url) is str:
            data = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ]
        else:
            data = [{"role": "user", "content": message}]
        history = data
        message: Message = Message()
        message.ask_id = str(uuid1())
        message.ask = data
        message.request_start_timestamp = start_time
        response = self._chat_completion(history)
        message.answer = [{"role": "assistant", "content": response}]
        message.request_end_timestamp = time.time()
        message.time_escaped = (
                message.request_end_timestamp - message.request_start_timestamp
        )

        # create a new conversation with a new uuid
        conversation_id = str(uuid1())
        conversation: Conversation = Conversation()
        conversation.conversation_id = conversation_id
        conversation.message_list.append(message)

        self.conversation_dict[conversation_id] = conversation

        return response, conversation_id

    # add retry handler to retry 1 more time if the API connection fails
    @retry(stop=stop_after_attempt(2))
    def send_message(
            self, message, conversation_id, image_url: str = None, debug_mode=False
    ):
        # create message history based on the conversation id
        chat_message = [
            {
                "role": "system",
                "content": "You are a helpful assistant",
            },
        ]
        conversation = self.conversation_dict[conversation_id]

        for _message in conversation.message_list[-self.history_length:]:
            chat_message.extend(_message.ask)
            chat_message.extend(_message.answer)
        # append the new message to the history
        # form the data that contains url
        if image_url is not None and type(image_url) is str:
            data = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ]
        else:
            data = [{"role": "user", "content": message}]
        chat_message.extend(data)
        # create the message object
        message: Message = Message()
        message.ask_id = str(uuid1())
        message.ask = data
        message.request_start_timestamp = time.time()
        # count the token cost
        # num_tokens = self._count_token(chat_message)
        # Get response. If the response is None, retry.
        response = self._chat_completion(chat_message)

        # update the conversation
        message.answer = [{"role": "assistant", "content": response}]
        message.request_end_timestamp = time.time()
        message.time_escaped = (
                message.request_end_timestamp - message.request_start_timestamp
        )
        conversation.message_list.append(message)
        self.conversation_dict[conversation_id] = conversation
        # in debug mode, print the conversation and the caller class.
        if debug_mode:
            print("Caller: ", inspect.stack()[1][3], "\n")
            print("Message:", message, "\n")
            print("Response:", response, "\n")
            # print("Token cost of the conversation: ", num_tokens, "\n")
        return response

class OPENAI:
    def __init__(self):
        self.conversation_dict: Dict[str, Conversation] = {}
        self.history_length = 5  # Max number of messages to retain in conversation history
        self.error_waiting_time = 3  # Retry waiting time in seconds

    def _chat_completion(self, history: List[dict]) -> str:
        try:
            client = OpenAI(api_key='', base_url='', timeout=600)

            response = client.chat.completions.create(
                model="",
                messages=history,
                temperature=0.5,
            )
            ans = response.choices[0].message.content
            return ans
        except Exception as e:
            logger.error(e)

    def send_new_message(self, message: str, image_url: str = None):
        # create a message
        start_time = time.time()
        if image_url is not None and type(image_url) is str:
            data = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ]
        else:
            data = [{"role": "user", "content": message}]
        history = data
        message: Message = Message()
        message.ask_id = str(uuid1())
        message.ask = data
        message.request_start_timestamp = start_time
        response = self._chat_completion(history)
        message.answer = [{"role": "assistant", "content": response}]
        message.request_end_timestamp = time.time()
        message.time_escaped = (
                message.request_end_timestamp - message.request_start_timestamp
        )

        # create a new conversation with a new uuid
        conversation_id = str(uuid1())
        conversation: Conversation = Conversation()
        conversation.conversation_id = conversation_id
        conversation.message_list.append(message)

        self.conversation_dict[conversation_id] = conversation

        return response, conversation_id

    # add retry handler to retry 1 more time if the API connection fails
    @retry(stop=stop_after_attempt(2))
    def send_message(
            self, message, conversation_id, image_url: str = None, debug_mode=False
    ):
        # create message history based on the conversation id
        chat_message = [
            {
                "role": "system",
                "content": "You are a helpful assistant",
            },
        ]
        conversation = self.conversation_dict[conversation_id]

        for _message in conversation.message_list[-self.history_length:]:
            chat_message.extend(_message.ask)
            chat_message.extend(_message.answer)
        # append the new message to the history
        # form the data that contains url
        if image_url is not None and type(image_url) is str:
            data = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ]
        else:
            data = [{"role": "user", "content": message}]
        chat_message.extend(data)
        # create the message object
        message: Message = Message()
        message.ask_id = str(uuid1())
        message.ask = data
        message.request_start_timestamp = time.time()
        # count the token cost
        # num_tokens = self._count_token(chat_message)
        # Get response. If the response is None, retry.
        response = self._chat_completion(chat_message)

        # update the conversation
        message.answer = [{"role": "assistant", "content": response}]
        message.request_end_timestamp = time.time()
        message.time_escaped = (
                message.request_end_timestamp - message.request_start_timestamp
        )
        conversation.message_list.append(message)
        self.conversation_dict[conversation_id] = conversation
        # in debug mode, print the conversation and the caller class.
        if debug_mode:
            print("Caller: ", inspect.stack()[1][3], "\n")
            print("Message:", message, "\n")
            print("Response:", response, "\n")
            # print("Token cost of the conversation: ", num_tokens, "\n")
        return response
