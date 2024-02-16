import datetime
import json
import random
import time
import urllib.parse as urlparse
import requests
import string
from django.http import HttpResponse, JsonResponse
from .base import Connector as ConnectorBase
import pytz
from django.utils import timezone
import base64
import json
import mimetypes
import random
import string
import tempfile
import time
from io import BytesIO

import qrcode
import zbarlight
from django import forms
from django.conf import settings
from django.db import IntegrityError
from django.http import JsonResponse
from django.template import Context, Template
from envelope.models import LiveChatRoom
from PIL import Image

from emojipy import emojipy


def log(msg: str):
    print("----------------")
    print(msg)
    print("----------------")

class Connector(ConnectorBase):
    support_active_chat = True

    def status_session(self,**kwargs):
        try:
            endpoint = self.config.get("endpoint")
            response = requests.get(
                f"{endpoint}/status",
            )
            response.raise_for_status()

            baileys_status = response.json()

            message = { "success": baileys_status["api"] == "ok", "message": baileys_status["socket"] }
        except Exception as err:
            message = { "success": False, "message": "Error: Unable to access Baileys API." }

        self.outcome_admin_message(f"{'Online' if message.get('success') else 'Offline'}: {message.get('message')}")

        return message
        
    def initialize(self,**kwargs):
        """
        Initialize session to server
        """
        pass

    def livechat_manager(self,**kwargs):
        pass

    def _get_message_type(self, msg):
        types = {
            "image/webp": msg.get("message", {}).get("stickerMessage", None),
            "text/text": msg.get("message", {}).get("conversation", None),
            "image/jpeg": msg.get("message", {}).get("imageMessage", None)
        }

        for key in types:
            if types[key]:
                return key

    def _parse_msg(self, msg):
        jid = msg.get("key", {}).get("remoteJid")
        id = msg.get("key", {}).get("id")
        message = msg.get("message", {}).get("conversation", None)
        name = msg.get("pushName")
        phone = jid.split("@")[0]

        return jid, name, message, phone, id

    def received_message(self, msg, **kwargs):
        jid, name, message, phone, id =  self._parse_msg(msg)

        self.phone = phone
        #update name

        self.register_message(envelope_id=id)
        self.get_rocket_client()
        room = self.get_room(phone=phone)

        message_types = {
            "image/webp": lambda room_id,msg: self.outcome_file_from_url(room_id, msg_type, msg.get("message", {}).get("stickerMessage",{}).get("url", "")),
            "text/text": lambda room_id,_: self.outcome_text(room_id, message),
            "image/jpeg": lambda room_id, msg: self.outcome_file_from_url(room_id, msg_type, msg.get("message", {}).get("imageMessage",{}).get("url", "")),
        }

        msg_type = "text/text" if message else self._get_message_type(msg)
        
        message_types[msg_type](room.room_id,msg)

    def incoming(self):
        self.logger_info(f"INCOMING MESSAGE: {self.message}")

        payload = self.message

        authenticated_actions = {
            "start": self.initialize,
            "status": self.status_session,
            "close": self.close_session,
            "livechat": self.livechat_manager,
        }

        actions = {
            "qr": self._qr,
            "connected": self._show_connected,
            "zapit": self._active_chat,
            "rc": self.rc_gateway,
            "incoming_message": self.received_message
        }

        action = payload.get("action", None)

        if action:
            if self.config.get("session_management_token"):
            # authenticated
                if payload.get("session_management_token") == self.config.get("session_management_token"):
                    response = authenticated_actions[action](**payload)
                    return JsonResponse({"action": action, "response": response})

                else:
                    return JsonResponse({"success": False, "message": "INVALID TOKEN"})
            
            # execute actions for: qr, connected
            actions[action](**payload)

        
        if self._get_config_token() and payload.get("token") == self._get_config_token():
            trigger_word = payload.get("trigger_word")
            req = actions[trigger_word](**payload)
            self.logger_info(req)
            return JsonResponse(req)
        

                
        return JsonResponse({})
    
    def rc_gateway(self, **kwargs):

        actions = {
            "status": self.status_session,
            "reset": self.reset_baileys_server
        }

        text = kwargs.get("text")

        action = text.split(" ")[1]

        return actions[action](**kwargs)
    
    def reset_baileys_server(self, **kwargs):
        endpoint = self.config.get("endpoint")

        response = requests.get(
            f"{endpoint}/hard-reset"
        )
        
        response.raise_for_status()

        text = (
                " :white_check_mark: :white_check_mark: :white_check_mark: "
                + "SUCESS!!!      :white_check_mark: :white_check_mark: :white_check_mark: "
            )
        self.outcome_admin_message(text)

    def _show_connected(self,**kwargs):
        text = (
                " :white_check_mark: :white_check_mark: :white_check_mark: "
                + "SUCESS!!!      :white_check_mark: :white_check_mark: :white_check_mark: "
            )
        self.outcome_admin_message(text)

    def _qr(self, **kwargs):
        base64_fixed_code = self.message.get("update", {}).get("qr", {})
        qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=40,
                border=5,
            )

        qr.add_data(base64_fixed_code)
        qr.make(fit=True)
        img = qr.make_image()

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        self.outcome_qrbase64(img_str)

    def _get_config_token(self):
        return self.config.get("active_chat_webhook_integration_token")
    
    def _check_number_status(self, number):
        endpoint = self.config.get("endpoint")
        
        start_session_req = requests.get(
            f"{endpoint}/onWA/{number}"
        )

        return start_session_req.json()
    
    def ingoing(self):
        self.logger_info(f"RECEIVED: {json.dumps(self.message)}")
    
        if self.message.get("type") == "LivechatSessionTaken":
            self.handle_livechat_session_taken()
    
        if self.message.get("type") == "LivechatSessionQueued":
            self.handle_livechat_session_queued()

        if self.message.get("type") == "Message":
            message, created = self.register_message()
            ignore_close_message = self.message_object.room.token in self.config.get(
                "ignore_token_force_close_message", ""
            ).split(",")
            if not message.delivered:
                
                phone = self.message.get("visitor").get("phone")[0].get("phoneNumber")
                for message in self.message.get("messages", []):
                    agent_name = self.get_agent_name(message)
                    phone = self.message.get("visitor").get("phone")[0].get("phoneNumber")
                
                    if message.get("closingMessage"):
                        self.get_rocket_client()
                        room_info = self.rocket.rooms_info(
                            room_id=self.message_object.room.room_id
                        ).json()
                        department = room_info.get("room", {}).get("departmentId")
                        message["msg"] = self.get_close_message(department=department)
                        if message.get("msg") and not ignore_close_message:
                            print("GOT IN!!!!")
                            if self.connector.config.get(
                                "add_agent_name_at_close_message"
                            ):
                                self.logger_info(self.message)
                                self.outgo_text_message(message, phone, agent_name=agent_name)
                            else:
                                self.logger_info(self.message)
                                self.outgo_text_message(message, phone)
                        else:
                            self.message_object.delivered = True
                            self.message_object.save()
                    
                        self.close_room()
                    else:
                    
                        if message.get("attachments", {}):
                        
                            self.outgo_file_message(message, phone, agent_name=agent_name)
                        else:
                            self.logger_info(self.message)
                            self.outgo_text_message(message, phone, agent_name=agent_name)
            else:
                self.logger_info("MESSAGE ALREADY SENT. IGNORING.")

    def _get_text_params(self, text):
        reference = text.split()[1]
        number = reference.split("@")[0]
        department = reference.split("@")[1]
        message = " ".join(text.split(" ")[2:])
        make_room = "@" in reference

        return reference, number, department, make_room, message
    
    def _department_not_found(self, department,room_id,message_id,text,check_number,message_raw,phone):
        # maybe department is an online agent. let's check if
        agents = self.rocket.livechat_get_users(
            user_type="agent"
        ).json()
        available_agents = [
            agent
            for agent in agents["users"]
            if agent["status"] == "online"
            and agent.get("statusLivechat") == "available"
        ]
        self.logger_info(
            "NO DEPARTMENT FOUND. LOOKING INTO ONLINE AGENTS: {}".format(
                available_agents
            )
        )
        for agent in available_agents:
            if agent.get("username").lower() == department.lower():
                return self._department_found(["AGENT-DIRECT:" + agent.get("_id")],check_number,message_raw,True,room_id,message_id,text,phone)
        
        # transfer the room for the agent
        available_usernames = [
            u["username"] for u in available_agents
        ]
        self.rocket.chat_update(
            room_id=room_id,
            msg_id=message_id,
            text=text
            + f"\n:warning: AGENT {department} NOT AVAILABLE OR ONLINE"
            + f"\nAVAILABLE AGENTS {available_usernames}",
        )
        return {
            "success": False,
            "message": f"AGENT {department} NOT AVAILABLE OR ONLINE",
            "available_agents": available_agents,
        }
        
    
    def _multiple_department_found(self,now_str,departments,department,room_id,message_id):
        alert_message = "\n:warning: {} More than one department found. Try one of the below:".format(
            now_str
        )
        for dpto in departments:
            alert_message = alert_message + "\n*{}*".format(
                self.message.get("text").replace(
                    "@" + department, "@" + dpto["name"]
                ),
            )
        self.rocket.chat_update(
            room_id=room_id,
            msg_id=message_id,
            text=self.message.get("text") + alert_message,
        )
        return {
            "success": False,
            "message": "MULTIPLE DEPARTMENTS FOUND",
            "departments": departments,
        }
    
    def _department_found(self,departments,check_number,message_raw,transfer,room_id,message_id,text, phone):
        department_id = None
        if "AGENT-DIRECT:" in departments[0]:
            agent_id = departments[0].split(":")[1]
            self.logger_info(f"AGENT-DIRECT TRIGGERED: {agent_id}")
            department = None
        else:
            department = departments[0]["name"]
            department_id = departments[0]["_id"]

        # define message type
        self.type = "active_chat"
        # register message
        message, created = self.register_message()
        # do not send a sent message
        if message.delivered:
            return {
                "success": False,
                "message": "MESSAGE ALREADY SENT",
            }
        # create basic incoming new message, as payload
        self.type = "incoming"
        self.message = {
            "from": check_number.get("jid"),
            "chatId": check_number.get("jid"),
            "id": self.message.get("jid"),
            "visitor": {
                "token": self.get_visitor_token(),
            },
        }
        # self.check_number_info(
        #     check_number["response"]["id"]["user"], augment_message=True
        # )
        self.logger_info(
            f"ACTIVE MESSAGE PAYLOAD GENERATED: {self.message}"
        )
        # if force transfer for active chat, for it.

        # register room
        room = self.get_room(
            department,
            allow_welcome_message=False,
            check_if_open=True,
            force_transfer=department_id,
            phone=phone
        )
        if room:
            self.logger_info(f"ACTIVE CHAT GOT A ROOM {room}")
            # send the message to the room, in order to be delivered to the
            # webhook and go the flow
            # send message_raw to the room
            self.get_rocket_client(bot=True)
            post_message = self.rocket.chat_post_message(
                text=message_raw, room_id=room.room_id
            )
            # change the message with checkmark
            if post_message.ok:
                if transfer:
                    payload = {
                        "roomId": room.room_id,
                        "userId": agent_id,
                    }
                    self.rocket.call_api_post(
                        "livechat/room.forward", **payload
                    )
                self.rocket.chat_update(
                    room_id=room_id,
                    msg_id=message_id,
                    text=":white_check_mark: " + text,
                )
                # register message delivered
                if self.message_object:
                    self.message_object.delivered = True
                    self.message_object.save()
                return {
                    "success": True,
                    "message": "MESSAGE SENT",
                }
            else:
                return {
                    "success": False,
                    "message": "COULD NOT SEND MESSAGE",
                }

        else:
            return {
                "success": False,
                "message": "COULD NOT CREATE ROOM",
            }
        
    def _direct_send_message(self, number, message_raw,room_id,message_id,text):
        # no department, just send the message
        self.message["chatId"] = number
        message = {"msg": message_raw}
        self.logger_info(self.message)
        sent = self.outgo_text_message(message,number)
        if sent and sent.ok:
            # return {
            #     "text": ":white_check_mark: SENT {0} \n{1}".format(
            #         number, message_raw
            #     )
            # }
            # update message
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=message_id,
                text=":white_check_mark: " + text,
            )
            return {"success": True, "message": "MESSAGE SENT"}
        else:
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=message_id,
                text=":warning: "
                + text
                + "\n ERROR WHILE SENDING MESSAGE",
            )
            return {"success": False, "message": "ERROR WHILE SENDING MESSAGE"}
     
    def _active_chat(self, message_id, text,**kwargs):
        # set the message type
        self.type = "active_chat"
        self.message["type"] = self.type
        department = False
        department_id = None
        transfer = False
        # get client
        self.get_rocket_client()
        now_str = datetime.datetime.now().replace(microsecond=0).isoformat()
        reference, number, department, make_room, message_raw = self._get_text_params(text)
        self.phone = number
        room_id = self.message.get("channel_id")

        # get the number, or all
        
        
        check_number = self._check_number_status(number)
        # could not get number validation
        if not check_number.get("exists", False):
            alert = f"COULD NOT SEND ACTIVE MESSAGE TO *{self.connector.name}*"
            self.logger_info(alert)
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=message_id,
                text=self.message.get("text") + f"\n:warning: {now_str} {alert}",
            )
            # return nothing
            return {"success": False, "message": "NO MESSAGE TO SEND"}
        # register number to get_visitor_id
        # emulating a regular ingoing message
        self.message["visitor"] = {"token": check_number.get("jid")}

        if not message_raw:
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=message_id,
                text=self.message.get("text")
                + "\n:warning: {} NO MESSAGE TO SEND. *SYNTAX: {} {} <TEXT HERE>*".format(
                    now_str, self.message.get("trigger_word"), reference
                ),
            )
            # return nothing
            return {"success": False, "message": "NO MESSAGE TO SEND"}

        # number checking
        if check_number.get("exists", False):
            # can receive messages
            if make_room:
                # check if department is valid
                if department:
                    department_check = self.rocket.call_api_get(
                        "livechat/department",
                        text=department,
                        onlyMyDepartments="false",
                    )
                    # departments found
                    departments = department_check.json().get("departments")

                    self.logger_info(departments)

                    if not departments:
                        return self._department_not_found(department,room_id,message_id,text,check_number,message_raw,number)
                    # > 1 departments found
                    if len(departments) > 1:
                        return self._multiple_department_found(now_str,departments,department,room_id,message_id)
                    # only one department, good to go.
                    if len(departments) == 1:
                        # direct chat to user
                        # override department, and get agent name
                        return self._department_found(departments,check_number,message_raw,transfer,room_id,message_id,text, number)

                # register visitor

            else:
                return self._direct_send_message(number, message_raw,room_id,message_id,text)

        # if cannot receive message, report
        else:
            # check_number failed, not a valid number
            # report back that it was not able to send the message
            # return {"text": ":warning:  INVALID NUMBER: {0}".format(number)}
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=message_id,
                text=self.message.get("text") + f"\n:warning: {now_str} INVALID NUMER",
            )
            return {"success": True, "message": "INVALID NUMBER"}

    def get_visitor_phone(self):
        self.logger.info(self.message)
        return self.message.get("chatId").split('@')[0]

    
    def outgo_text_message(self, message, phone=None, agent_name=None):
        endpoint = self.config.get("endpoint")

        self.logger_info(self.message)

        content = message if type(message) == str else message["msg"] 
        
        if not phone:
            phone = self.get_visitor_phone()
        
        response = requests.post(
            f"{endpoint}/send-message/{phone}@s.whatsapp.net",
            data={"message":content}
        )

        return response
    
    def outgo_file_message(self, message, phone=None, agent_name=None):
        endpoint = self.config.get("endpoint")
        file_url = (
            self.connector.server.url
            + message["attachments"][0]["title_link"]
            + "?"
            + urlparse.urlparse(message["fileUpload"]["publicFilePath"]).query
        )
        mime = self.message["messages"][0]["fileUpload"]["type"]
        
        if not phone:
            phone = self.get_visitor_phone()

        payload = {
            "mime": mime,
            "url": file_url
        }
        response = requests.post(
            f"{endpoint}/send-media/{phone}@s.whatsapp.net",
            data=payload
        )

        response.raise_for_status()
        
        timestamp = int(time.time())
        self.message_object.payload[timestamp] = payload
        self.message_object.delivered = True
        self.message_object.response[timestamp] = payload
        self.message_object.save()

    def logger_info(self, message):
        output = f"{self.connector} > {self.type.upper()} > {message}"
        if self.message:
            if self.get_message_id():
                output = f"MESSAGE ID {self.get_message_id()} > " + output
        self.logger.info(output)

    def logger_error(self, message):
        self.logger.error(f"{self.connector} > {self.type.upper()} > {message}")

    def outcome_file_from_url(self,room_id, mime, file_url, description=None):
        filename = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        response_arquivo = requests.get(file_url, stream=True)
        stream_arquivo = BytesIO(response_arquivo.content)
        files = {"file": (filename, stream_arquivo, mime)}
        headers = {"x-visitor-token": self.get_visitor_token()}

        url = "{}/api/v1/livechat/upload/{}".format(
            self.connector.server.url, room_id
        )

        data = {}
        if description:
            data["description"] = description

        return self._outcome_file(room_id, description, headers, files, data, url)


    def outcome_file(self, base64_data, room_id, mime,filename=None, description=None):
        
        filename = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        filedata = base64.b64decode(base64_data)
        extension = mimetypes.guess_extension(mime)
    
    
        with tempfile.NamedTemporaryFile(suffix=extension) as tmp:
            tmp.write(filedata)
            headers = {"x-visitor-token": self.get_visitor_token()}

            files = {"file": (filename, open(tmp.name, "rb"), mime)}
            data = {}
            if description:
                data["description"] = description
            url = "{}/api/v1/livechat/upload/{}".format(
                self.connector.server.url, room_id
            )
            return self._outcome_file(room_id, description, headers, files, data, url)

    def _outcome_file(self, room_id, description, headers, files, data, url):
        deliver = requests.post(url, headers=headers, files=files, data=data)
        log(files)
        self.logger_info(f"RESPONSE OF FILE OUTCOME: {deliver.json()}")
        timestamp = int(time.time())
        if self.message_object:
            self.message_object.payload[timestamp] = {
                    "data": "sent attached file to rocketchat"
                }
        if deliver.ok:
            if settings.DEBUG and deliver.ok:
                print("teste, ", deliver)
                print("OUTCOME FILE RESPONSE: ", deliver.json())
            self.message_object.response[timestamp] = deliver.json()
            self.message_object.delivered = deliver.ok
            self.message_object.save()

        if self.connector.config.get(
                "outcome_attachment_description_as_new_message", True
            ):
            if description:
                description_message_id = self.get_message_id() + "_description"
                self.outcome_text(
                        room_id, description, message_id=description_message_id
                    )

        return deliver

    def outcome_text(self, room_id, text, message_id=None):
        deliver = self.room_send_text(room_id, text, message_id)
        timestamp = int(time.time())
        if self.message_object:
            self.message_object.payload[timestamp] = json.loads(deliver.request.body)
            self.message_object.response[timestamp] = deliver.json()
        
        if deliver.ok:
            
            if self.message_object:
                self.message_object.delivered = True
                self.message_object.room = self.room
                self.message_object.save()
            return deliver
        else:
            self.logger_info("MESSAGE *NOT* DELIVERED...")
        
            if self.message_object:
                self.message_object.save()
        
            r = deliver.json()
        
        
            if r.get("error", "") in ["room-closed", "invalid-room", "invalid-token"]:
                self.room_close_and_reintake(self.room)
            return deliver

    def get_qrcode_from_base64(self, qrbase64):
        try:
            data = qrbase64.split(",")[1]
        except IndexError:
            data = qrbase64
        img = Image.open(BytesIO(base64.b64decode(data)))
        code = zbarlight.scan_codes(["qrcode"], img)[0]
        return code

    def generate_qrcode(self, code):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=40,
            border=5,
        )

        qr.add_data(code)
        qr.make(fit=True)
        img = qr.make_image()

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return img_str

    def get_visitor_name(self):
        try:
            name = self.message.get("data", {}).get("sender", {}).get("name")
        except IndexError:
            name = "Duda Nogueira"
        return name

    def get_visitor_username(self,phone=None):
        try:
            if not phone:
                phone = self.message.get("data", {}).get("from")

            visitor_username = f"{phone}@s.whatsapp.net"
        except IndexError:
            visitor_username = "channel:visitor-username"
        return visitor_username

    def get_visitor_json(self, department=None,phone=None):
        # visitor_name = self.get_visitor_name()
        visitor_name = self.get_visitor_username(phone)
        visitor_username = self.get_visitor_username(phone)
        visitor_phone = self.get_visitor_phone() if not phone else phone
        visitor_token = self.get_visitor_token()
        if not department:
            department = self.connector.department
        connector_name = self.connector.name

        visitor = {
            "username": visitor_username,
            "token": visitor_token,
            "phone": visitor_phone,
            "customFields": [
                {
                    "key": "connector_name",
                    "value": connector_name,
                    "overwrite": True,
                },
            ],
        }
        if department:
            visitor["department"] = department

        if visitor_name:
            visitor["customFields"].append(
                {
                    "key": "whatsapp_name",
                    "value": visitor_name,
                    "overwrite": self.config.get("overwrite_custom_fields", True),
                }
            )

        if visitor_phone:
            visitor["customFields"].append(
                {
                    "key": "whatsapp_number",
                    "value": visitor_phone,
                    "overwrite": self.config.get("overwrite_custom_fields", True),
                }
            )

        if visitor_name and not self.config.get("supress_visitor_name", False):
            visitor["name"] = visitor_name

        

        return visitor

    def get_incoming_visitor_id(self):
        if self.message.get("event") == "onIncomingCall":
        
            return self.message.get("data", {}).get("peerJid")
        else:
            return self.message.get("data", {}).get("from")

    def get_visitor_id(self):
        if self.type == "incoming":
            visitor_id = self.get_incoming_visitor_id()
        else:
            visitor_id = self.message.get("visitor", {}).get("token").split(":")[1]
        visitor_id = str(visitor_id).strip()
        return visitor_id
    
    def get_visitor_token(self):
        try:
            phone = self.phone
            if not self.phone:
                phone = self.get_visitor_id()
            visitor_id = f"whatsapp:{phone}"
            return visitor_id
        except IndexError:
            return "channel:visitor-id"

    def get_room(
        self,
        department=None,
        create=True,
        allow_welcome_message=True,
        check_if_open=False,
        force_transfer=None,
        phone=None
    ):
        open_rooms = None
        room = None
        room_created = False
        connector_token = self.get_visitor_token()
        self.logger_info(phone)

    
        if self.config.get("ignore_visitors_token"):
            if connector_token in self.config.get("ignore_visitors_token").split(","):
                self.logger_info(f"Ignoring visitor token {connector_token}")
                return room

        try:
            room = LiveChatRoom.objects.get(
                connector=self.connector, token=connector_token, open=True
            )
            self.logger_info(f"get_room, got {room}")
            if check_if_open:
                self.logger_info("checking if room is open")
                open_rooms = self.rocket.livechat_rooms(open="true").json()
                open_rooms_id = [r["_id"] for r in open_rooms["rooms"]]
                if room.room_id not in open_rooms_id:
                    self.logger_info(
                        "room was open in Rocket.Connect, but not in Rocket.Chat"
                    )
                
                    room.open = False
                    room.save()
                    raise LiveChatRoom.DoesNotExist

        except LiveChatRoom.MultipleObjectsReturned:
        
        
            return (
                LiveChatRoom.objects.filter(
                    connector=self.connector, token=connector_token, open=True
                )
                .order_by("-created")
                .last()
            )
        except LiveChatRoom.DoesNotExist:
            if create:
                self.logger_info("get_room, didn't got room")
                if self.config.get("open_room", True):
                
                
                    visitor_json = self.get_visitor_json(department,phone)

                    self.logger_info(f"VISITOR JSON: {visitor_json}")
                    self.logger_info(f"VISITOR token: {connector_token}")
                
                    visitor_object = self.rocket.livechat_register_visitor(
                        visitor=visitor_json, token=connector_token
                    )
                    response = visitor_object.json()
                    self.logger_info(f"VISITOR RESPONSE: {response}")
                    
                
                
                    if response["success"]:
                        rc_room = self.rocket.livechat_room(token=connector_token)
                        rc_room_response = rc_room.json()
                        self.logger_info(rc_room_response)
                        
                        if rc_room_response["success"]:
                            room = LiveChatRoom.objects.create(
                                connector=self.connector,
                                token=connector_token,
                                room_id=rc_room_response["room"]["_id"],
                                open=True,
                            )
                            room_created = True
                        else:
                            if rc_room_response["errorType"] == "no-agent-online":
                                self.logger_info("NO AGENTS ONLINE")
                                if self.config.get("no_agent_online_alert_admin"):
                                
                                    template = Template(
                                        self.config.get("no_agent_online_alert_admin")
                                    )
                                    context = Context(self.message)
                                    message = template.render(context)
                                    self.outcome_admin_message(message)
                                if self.config.get(
                                    "no_agent_online_autoanswer_visitor"
                                ):
                                    template = Template(
                                        self.config.get(
                                            "no_agent_online_autoanswer_visitor"
                                        )
                                    )
                                    context = Context(self.message)
                                    message = {"msg": template.render(context)}
                                    self.outgo_text_message(message)
                                
        self.room = room
    
        if force_transfer:
            payload = {
                "rid": self.room.room_id,
                "token": self.room.token,
                "department": force_transfer,
            }
            force_transfer_response = self.rocket.call_api_post(
                "livechat/room.transfer", **payload
            )
            if force_transfer_response.ok:
                self.logger_info(f"Force Transfer Response: {force_transfer_response}")
            else:
                self.logger_error(f"Force Transfer ERROR: {force_transfer_response}")

    
        if allow_welcome_message:
            if self.config.get("welcome_message"):
            
            
            
                if (
                    not self.config.get("open_room", True)
                    and self.config.get("welcome_message")
                ) or (
                    self.config.get("open_room", True)
                    and room_created
                    and self.config.get("welcome_message")
                ):
                
                    if room_created:
                        payload = {
                            "rid": self.room.room_id,
                            "msg": self.config.get("welcome_message"),
                        }
                        a = self.outgo_message_from_rocketchat(payload)
                        print("AQUI! ", a)
                        self.logger_info(
                            "OUTWENT welcome message from Rocket.Chat " + str(payload)
                        )
                
                    else:
                        message = {"msg": self.config.get("welcome_message")}
                        self.outgo_text_message(message)

            if self.config.get("welcome_vcard") != {}:

                if (
                    not self.config.get("open_room", True)
                    and self.config.get("welcome_vcard")
                ) or (
                    self.config.get("open_room", True)
                    and room_created
                    and self.config.get("welcome_vcard")
                ):
                    payload = self.config.get("welcome_vcard")
                    self.outgo_vcard(payload)
                
                    if room and self.config.get(
                        "alert_agent_of_automated_message_sent", False
                    ):
                    
                        self.outcome_text(
                            room_id=room.room_id,
                            text="VCARD SENT: {}".format(
                                self.config.get("welcome_vcard")
                            ),
                            message_id=self.get_message_id() + "VCARD",
                        )
    
        if self.message_object:
            self.message_object.room = room
            self.message_object.save()

        return room

    def room_close_and_reintake(self, room):
        if settings.DEBUG:
            print("ROOM IS CLOSED. CLOSING AND REINTAKING")
        room.open = False
        room.save()
    
    
        self.incoming()

    def room_send_text(self, room_id, text, message_id=None):
        
        if not message_id:
            message_id = self.get_message_id()
        rocket = self.get_rocket_client()
        response = rocket.livechat_message(
            token=self.get_visitor_token(),
            rid=room_id,
            msg=text,
            _id=message_id,
        )
        
        return response

    def register_message(self, type=None, envelope_id=None):
        self.logger_info(f"REGISTERING MESSAGE: {json.dumps(self.message)}")
        try:
            if not type:
                type = self.type

            if not envelope_id:
                envelope_id = self.get_message_id()

            log(envelope_id)
            self.message_object, created = self.connector.messages.get_or_create(
                envelope_id=envelope_id, type=type
            )
            self.message_object.raw_message = self.message
            if not self.message_object.room:
                self.message_object.room = self.room
            self.message_object.save()
            if created:
                self.logger_info(f"NEW MESSAGE REGISTERED: {self.message_object.id}")
            else:
                self.logger_info(
                    f"EXISTING MESSAGE REGISTERED: {self.message_object.id}"
                )
            return self.message_object, created
        except IntegrityError as err:
            self.logger_info(
                f"CANNOT CREATE THIS MESSAGE AGAIN: {self.get_message_id()} - {err}"
            )
            return "", False

    def get_message_id(self):
        
        if self.type == "active_chat":
            return self.message.get("message_id", self.get_incoming_message_id())
        
        if self.type == "ingoing":
        
            if self.message["messages"]:
                rc_message_id = self.message["messages"][0]["_id"]
                return rc_message_id
        
            if self.message.get("_id"):
                return self.message.get("_id")

    
        return self.get_incoming_message_id()

    def get_incoming_message_id(self):
        try:
            message_id = self.message.get("key", {}).get("id")
        except IndexError:
            message_id = "".join(random.choice(string.ascii_letters) for i in range(10))

        return message_id

    def get_message_body(self):
        try:
        
            message_body = self.message.get("data", {}).get("body")
        except IndexError:
            message_body = "New Message: {}".format(
                "".join(random.choice(string.ascii_letters) for i in range(10))
            )
        return message_body

    def get_rocket_client(self, bot=False, force=False):
    
    
        if not self.rocket or force:
            try:
                self.rocket = self.connector.server.get_rocket_client(bot=bot)
            except requests.exceptions.ConnectionError:
            
                self.rocket_down()
                self.rocket = False
        return self.rocket

    def outgo_message_from_rocketchat(self, payload):
        self.get_rocket_client(bot=True, force=True)
        return self.rocket.chat_send_message(payload)

    def rocket_down(self):
        if settings.DEBUG:
            print("DO SOMETHING FOR WHEN ROCKETCHAT SERVER IS DOWN")

    def joypixel_to_unicode(self, content):
        return emojipy.Emoji().shortcode_to_unicode(content)


    def decrypt_media(self, message_id=None):
        if not message_id:
            message_id = self.get_message_id()
        url_decrypt = "{}/decryptMedia".format(self.config["endpoint"])
        payload = {"args": {"message": message_id}}
        s = self.get_request_session()
        decrypted_data_request = s.post(url_decrypt, json=payload)
    
        data = None
        if decrypted_data_request.ok:
            response = decrypted_data_request.json().get("response", None)
            
            if response:
                data = response.split(",")[1]
        return data

    def close_room(self):
        if self.room:
        
            LiveChatRoom.objects.filter(
                connector__server=self.connector.server, room_id=self.room.room_id
            ).update(open=False)
            self.post_close_room()

    def post_close_room(self):
        """
        Method that runs after the room is closed
        """
        if settings.DEBUG:
            print("Do stuff after the room is closed")

    def get_agent_name(self, message):
        agent_name = message.get("u", {}).get("name", {})
        agent_username = message.get("u", {}).get("username", {})
    
        supress = self.config.get("supress_agent_name", None)
        if supress:
            if supress == "*" or agent_username in supress.split(","):
                agent_name = None

        return self.change_agent_name(agent_name)

    def render_message_agent_template(self, message, agent_name):
        context = {"message": message, "agent_name": agent_name}
        context = Context(context)
        default_message_template = "*[{{agent_name}}]*\n{{message}}"
        message_template = self.config.get("message_template", default_message_template)
        template = Template(message_template)
        message = template.render(context)
        return message

    def get_close_message(self, department=None):
        """
        get the close message configured for the connector
        """
        close_message = None
        force_close_message = self.config.get("force_close_message", None)
        advanced_force_close_message = self.config.get(
            "advanced_force_close_message", None
        )
        if force_close_message:
            close_message = force_close_message
        if advanced_force_close_message:
        
            if not department:
                close_message = force_close_message
            else:
                try:
                    close_message = self.config.get(
                        "advanced_force_close_message", None
                    ).get(department, None)
                except KeyError:
                    close_message = None
        self.logger_info(f"GOT CLOSE MESSAGE: {close_message}")
        return close_message

    def change_agent_name(self, agent_name):
        return agent_name

    def outgo_vcard(self, vcard_json):
        self.logger_info(f"OUTGOING VCARD {vcard_json}")

    def handle_incoming_call(self):
        if self.config.get("auto_answer_incoming_call"):
            self.logger_info(
                "auto_answer_incoming_call: {}".format(
                    self.config.get("auto_answer_incoming_call")
                )
            )
            message = {"msg": self.config.get("auto_answer_incoming_call")}
            self.outgo_text_message(message)
        if self.config.get("convert_incoming_call_to_text"):
            if self.room:
                self.outcome_text(
                    self.room.room_id,
                    text=self.config.get("convert_incoming_call_to_text"),
                )
    
        m = self.message_object
        m.delivered = True
        m.save()
        self.message_object = m
        self.logger_info(
            "handle_incoming_call marked message {} as read".format(
                self.message_object.id
            )
        )

    def handle_ptt(self):
        if self.config.get("auto_answer_on_audio_message"):
            self.logger_info(
                "auto_answer_on_audio_message: {}".format(
                    self.config.get("auto_answer_on_audio_message")
                )
            )
            message = {"msg": self.connector.config.get("auto_answer_on_audio_message")}
            self.outgo_text_message(message)
        if self.config.get("convert_incoming_audio_to_text"):
            if self.room:
                self.outcome_text(
                    self.room.room_id,
                    text=self.config.get("convert_incoming_audio_to_text"),
                )

    def handle_livechat_session_queued(self):
        self.logger_info("HANDLING LIVECHATSESSION QUEUED")

    def handle_livechat_session_taken(self):
        self.logger_info("HANDLING LIVECHATSESSION TAKEN")
        if self.config.get("session_taken_alert_template"):
        
            ignore_departments = self.config.get(
                "session_taken_alert_ignore_departments"
            )
            if ignore_departments:
                transferred_department = self.message.get("visitor", {}).get(
                    "department"
                )
                departments_list = ignore_departments.split(",")
                ignore_departments = [i for i in departments_list]
                if transferred_department in ignore_departments:
                    self.logger_info(
                        "IGNORING LIVECHATSESSION Alert for DEPARTMENT {}".format(
                            self.message.get("department")
                        )
                    )
                
                    return {
                        "success": False,
                        "message": "Ignoring department {}".format(
                            self.message.get("department")
                        ),
                    }
            self.get_rocket_client()
        
            department = self.rocket.call_api_get(
                "livechat/department/{}".format(self.message.get("departmentId"))
            ).json()
            self.message["department"] = department["department"]
            template = Template(self.config.get("session_taken_alert_template"))
            context = Context(self.message)
            message = template.render(context)
            message_payload = {"msg": str(message)}
            if (
                self.config.get("alert_agent_of_automated_message_sent", False)
                and self.room
            ):
            
                self.outcome_text(
                    self.room.room_id,
                    f"MESSAGE SENT: {message}",
                    message_id=self.get_message_id() + "SESSION_TAKEN",
                )
            outgo_text_obj = self.outgo_text_message(message_payload)
            self.logger_info(f"HANDLING LIVECHATSESSION TAKEN {outgo_text_obj}")
            return outgo_text_obj

    def handle_inbound(self, request):
        """
        this method will handle inbound payloads
        you can return

        {"success": True, "redirect":"http://rocket.chat"}

        for redirecting to a new page.
        """
        self.logger_info("HANDLING INBOUND, returning default")

        self.logger_info(request)
        return {"success": True, "redirect": "http://rocket.chat"}