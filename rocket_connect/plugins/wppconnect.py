import base64
import datetime
import json
import time
import urllib.parse as urlparse

import requests
from django import forms
from django.conf import settings
from django.core import validators
from django.http import HttpResponse, JsonResponse
from instance import tasks

from .base import BaseConnectorConfigForm
from .base import Connector as ConnectorBase


class Connector(ConnectorBase):
    """
        how to run wa-automate:
        npx @open-wa/wa-automate    -w 'http://127.0.0.1:8000/connector/4333bd76-519f-4fe8-b51a-a228b29a4e19' \
                                    -e 'http://127.0.0.1:8000/connector/4333bd76-519f-4fe8-b51a-a228b29a4e19' \
                                    --session-id 'test-session' \
                                    --kill-client-on-logout \
                                    --event-mode
    """

    def populate_config(self):
        self.connector.config = {
            "webhook": "http://127.0.0.1:8000/connector/WPP_EXTERNAL_TOKEN/",
            "endpoint": "http://wppconnect:8080",
            "secret_key": "My53cr3tKY",
            "instance_name": "test",
        }
        self.save()

    def generate_token(self):
        # generate token
        endpoint = "{0}/api/{1}/{2}/generate-token".format(
            self.config.get("endpoint"),
            self.config.get("instance_name"),
            self.config.get("secret_key"),
        )
        token = requests.post(endpoint)
        if token.ok:
            token = token.json()
            self.connector.config["token"] = token
            self.connector.save()
            return token
        return False

    def status_session(self):
        # generate token
        if self.config.get("endpoint"):
            endpoint = "{0}/api/{1}/status-session".format(
                self.config.get("endpoint"),
                self.config.get("instance_name"),
            )
            token = self.config.get("token", {}).get("token")
            if not token:
                self.generate_token()
                token = self.config.get("token", {}).get("token")

            if token:
                headers = {"Authorization": "Bearer " + token}
                status_req = requests.get(endpoint, headers=headers)
                if status_req.ok:
                    status = status_req.json()
                    return status
            else:
                return "Could not get token. Check the WPPConnect Secret Key"
        return False

    def close_session(self):
        # generate token
        endpoint = "{0}/api/{1}/close-session".format(
            self.config.get("endpoint"),
            self.config.get("instance_name"),
        )
        token = self.config.get("token", {}).get("token")
        if not token:
            self.generate_token()
            token = self.config.get("token", {}).get("token")

        headers = {"Authorization": "Bearer " + token}
        status_req = requests.post(endpoint, headers=headers)
        if status_req.ok:
            status = status_req.json()
            return status
        return False

    def check_number_status(self, number):
        endpoint = "{0}/api/{1}/check-number-status/{2}".format(
            self.config.get("endpoint"), self.config.get("instance_name"), number
        )

        token = self.config.get("token", {}).get("token")

        if not token:
            self.generate_token()
            token = self.config.get("token", {}).get("token")

        headers = {"Authorization": "Bearer " + token}
        data = {"webhook": self.config.get("webhook")}
        try:
            start_session_req = requests.get(endpoint, headers=headers, json=data)
            self.logger.info(
                "CHECKING NUMBER: {0}: {1}".format(number, start_session_req.json())
            )
            return start_session_req.json()
        except requests.ConnectionError:
            return {"success": False, "message": "Could not connect to wppconnect"}

    def check_number_info(self, number, augment_message=False):
        """
        this method will get infos from the contact api and insert
        into self message
        """
        endpoint = "{0}/api/{1}/contact/{2}".format(
            self.config.get("endpoint"), self.config.get("instance_name"), number
        )

        token = self.config.get("token", {}).get("token")

        if not token:
            self.generate_token()
            token = self.config.get("token", {}).get("token")

        headers = {"Authorization": "Bearer " + token}
        data = {"webhook": self.config.get("webhook")}
        number_info_req = requests.get(endpoint, headers=headers, json=data)
        number_info = number_info_req.json()
        self.logger.info(
            "CHECKING CONTACT INFO FOR  NUMBER {0}: {1}".format(number, number_info)
        )
        if augment_message:
            if not self.message.get("sender"):
                self.message["sender"] = {}
            name_order = self.config.get(
                "name_extraction_order", "pushname,name,shortName"
            )
            for order in name_order.split(","):
                if number_info.get("response", {}).get(order, False):
                    self.message["sender"][order] = number_info.get("response", {}).get(
                        order
                    )

        return number_info_req.json()

    def active_chat(self):
        """
        this method will be triggered when an active_chat needs to be places
        it has to interpret the active chat text, and do the necessary check and
        returns in order to provide.
        this method will provide options for:
        triggerword reference text
        reference can be:
            +5531111111@Department - opens a new chat at the selected department
            +5531111111@ Opens a new chat at the configured connector default department or None
        """
        # set the message type
        self.type = "active_chat"
        self.message["type"] = self.type
        department = False
        transfer = False
        # get client
        self.get_rocket_client()
        now_str = datetime.datetime.now().replace(microsecond=0).isoformat()
        # get the number reference, room_id and message_id
        reference = self.message.get("text").split()[1]
        room_id = self.message.get("channel_id")
        msg_id = self.message.get("message_id")
        # get the number, or all
        number = reference.split("@")[0]
        # register number to get_visitor_id
        # emulating a regular ingoing message
        self.message["visitor"] = {"token": "whatsapp:" + number}
        check_number = self.check_number_status(number)
        # could not get number validation
        if (
            not check_number.get("response")
            and check_number.get("status") == "Disconnected"
        ) or not check_number.get("success", True):
            alert = "CONNECTOR *{0}* IS DISCONNECTED".format(self.connector.name)
            self.logger_info(alert)
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=msg_id,
                text=self.message.get("text")
                + "\n:warning: {0} {1}".format(now_str, alert),
            )
            # return nothing
            return {"success": False, "message": "NO MESSAGE TO SEND"}
        # construct message
        texto = self.message.get("text")
        message_raw = " ".join(texto.split(" ")[2:])
        if not message_raw:
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=msg_id,
                text=self.message.get("text")
                + "\n:warning: {0} NO MESSAGE TO SEND. *SYNTAX: {1} {2} <TEXT HERE>*".format(
                    now_str, self.message.get("trigger_word"), reference
                ),
            )
            # return nothing
            return {"success": False, "message": "NO MESSAGE TO SEND"}

        # number checking
        if check_number.get("response", {}).get("canReceiveMessage", False):
            # can receive messages
            if "@" in reference:
                # a new room was asked to be created (@ included)
                try:
                    department = reference.split("@")[1]
                except IndexError:
                    # no department provided
                    department = None
                # check if department is valid
                if department:
                    department_check = self.rocket.call_api_get(
                        "livechat/department", text=department, onlyMyDepartments=False
                    )
                    # departments found
                    departments = department_check.json().get("departments")
                    if not departments:
                        # maybe department is an online agent. let's check if
                        agents = self.rocket.livechat_get_users(
                            user_type="agent"
                        ).json()
                        available_agents = [
                            agent
                            for agent in agents["users"]
                            if agent["status"] == "online"
                            and agent["statusLivechat"] == "available"
                        ]
                        self.logger_info(
                            "NO DEPARTMENT FOUND. LOOKING INTO ONLINE AGENTS: {0}".format(
                                available_agents
                            )
                        )
                        for agent in available_agents:
                            if agent.get("username").lower() == department.lower():
                                transfer = True
                                departments = ["AGENT-DIRECT:" + agent.get("_id")]
                        # transfer the room for the agent
                        if not transfer:
                            self.rocket.chat_update(
                                room_id=room_id,
                                msg_id=msg_id,
                                text=self.message.get("text")
                                + "\n:warning: {0} NO DEPARTMENT FOUND".format(now_str),
                            )
                            return {
                                "success": False,
                                "message": "NO DEPARTMENT FOUND",
                                "available_agents": available_agents,
                            }
                    # > 1 departments found
                    if len(departments) > 1:
                        alert_message = "\n:warning: {0} More than one department found. Try one of the below:".format(
                            now_str
                        )
                        for dpto in departments:
                            alert_message = alert_message + "\n*{0}*".format(
                                self.message.get("text").replace(
                                    "@" + department, "@" + dpto["name"]
                                ),
                            )
                        self.rocket.chat_update(
                            room_id=room_id,
                            msg_id=msg_id,
                            text=self.message.get("text") + alert_message,
                        )
                        return {
                            "success": False,
                            "message": "MULTIPLE DEPARTMENTS FOUND",
                            "departments": departments,
                        }
                    # only one department, good to go.
                    if len(departments) == 1:
                        # direct chat to user
                        # override department, and get agent name
                        if "AGENT-DIRECT:" in departments[0]:
                            self.logger_info("AGENT-DIRECT TRIGGERED")
                            department = None
                            agent_id = departments[0].split(":")[1]
                        else:
                            department = departments[0]["name"]

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
                            "from": check_number.get("response")
                            .get("id")
                            .get("_serialized"),
                            "chatId": check_number.get("response")
                            .get("id")
                            .get("_serialized"),
                            "id": self.message.get("message_id"),
                            "visitor": {
                                "token": "whatsapp:"
                                + check_number["response"]["id"]["_serialized"]
                            },
                        }
                        # TODO: known issue:
                        # it doesn't get client info when the client never sent a message
                        # possible hacky fix is send a message to the user first
                        # then get number info and then augment message
                        # augment name from contact API
                        self.check_number_info(
                            check_number["response"]["id"]["user"], augment_message=True
                        )
                        self.logger_info(
                            "ACTIVE MESSAGE PAYLOAD GENERATED: {0}".format(self.message)
                        )
                        # register room
                        room = self.get_room(department)
                        if room:
                            self.logger_info("ACTIVE CHAT GOT A ROOM {0}".format(room))
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
                                    msg_id=msg_id,
                                    text=":white_check_mark: " + texto,
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

                # register visitor

            else:
                # no department, just send the message
                self.message["chatId"] = number
                message = {"msg": message_raw}
                sent = self.outgo_text_message(message)
                if sent and sent.ok:
                    # return {
                    #     "text": ":white_check_mark: SENT {0} \n{1}".format(
                    #         number, message_raw
                    #     )
                    # }
                    # update message
                    self.rocket.chat_update(
                        room_id=room_id,
                        msg_id=msg_id,
                        text=":white_check_mark: " + self.message.get("text"),
                    )
                    return {"success": True, "message": "MESSAGE SENT"}
                else:
                    self.rocket.chat_update(
                        room_id=room_id,
                        msg_id=msg_id,
                        text=":warning: "
                        + self.message.get("text")
                        + "\n ERROR WHILE SENDING MESSAGE",
                    )
                    return {"success": False, "message": "ERROR WHILE SENDING MESSAGE"}

        # if cannot receive message, report
        else:
            # check_number failed, not a valid number
            # report back that it was not able to send the message
            # return {"text": ":warning:  INVALID NUMBER: {0}".format(number)}
            self.rocket.chat_update(
                room_id=room_id,
                msg_id=msg_id,
                text=self.message.get("text")
                + "\n:warning: {0} INVALID NUMER".format(now_str),
            )
            return {"success": True, "message": "INVALID NUMBER"}

    def start_session(self):
        endpoint = "{0}/api/{1}/start-session".format(
            self.config.get("endpoint"),
            self.config.get("instance_name"),
        )

        token = self.config.get("token", {}).get("token")

        if not token:
            self.generate_token()
            token = self.config.get("token", {}).get("token")

        headers = {"Authorization": "Bearer " + token}
        data = {"webhook": self.config.get("webhook")}
        start_session_req = requests.post(endpoint, headers=headers, json=data)
        if start_session_req.ok:
            start_session = start_session_req.json()
            return start_session
        return False

    def initialize(self):
        """
        c = Connector.objects.get(pk=12)
        cls = c.get_connector_class()
        ci = cls(connector=c, message={}, type="incoming")
        """
        # generate token
        try:
            self.generate_token()
        except requests.ConnectionError:
            return {"success": False, "message": "ConnectionError"}
        # start session
        return self.start_session()

    def incoming(self):
        """
        this method will process the incoming messages
        and ajust what necessary, to output to rocketchat
        """
        self.logger_info("INCOMING MESSAGE: {0}".format(self.message))
        # qr code
        if self.message.get("event") == "qrcode":
            base64_fixed_code = self.message.get("qrcode")
            self.outcome_qrbase64(base64_fixed_code)

        # admin message
        if self.message.get("event") == "status-find":
            text = "Session: {0}. Status: {1}".format(
                self.message.get("session"), self.message.get("status")
            )
            if self.message.get("status") in ["isLogged", "inChat"]:
                text = (
                    text
                    + ":white_check_mark::white_check_mark::white_check_mark:"
                    + "SUCESS!!!      :white_check_mark::white_check_mark::white_check_mark:"
                )
                # call intake unread task
                if self.config.get("process_unread_messages_on_start", False):
                    tasks.intake_unread_messages.delay(self.connector.id)
            self.outcome_admin_message(text)

        if self.message.get("event") == "incomingcall":
            # handle incoming call
            self.get_rocket_client()
            message, created = self.register_message()
            room = self.get_room()
            self.handle_incoming_call()

        # message
        if self.message.get("event") in ["onmessage", "unreadmessages"]:
            department = None
            if self.message.get("event") == "unreadmessages":
                self.logger_info(
                    "PROCESSING UNREAD MESSAGE. PAYLOAD {0}".format(self.message)
                )
                # if it's a message from Me, ignore:
                if self.message.get("id", {}).get("fromMe"):
                    return JsonResponse({})
                # adapt unread messages to intake like a regular message
                pass
            # direct messages only
            if not self.message.get(
                "isGroupMsg", False
            ) and "status@broadcast" not in self.message.get("from"):
                # register message
                message, created = self.register_message()
                if not message.delivered:
                    # get rocket client
                    self.get_rocket_client()
                    if not self.rocket:
                        return HttpResponse("Rocket Down!", status=503)
                    # department triage is enabled
                    if self.config.get("department_triage"):
                        # message has no department, always
                        # outcome department_triage_payload
                        # if it's not a button reply
                        # there is not room
                        room = self.get_room(department, create=False)
                        if not room:
                            # get departments and buttons
                            buttons = []
                            departments = self.rocket.call_api_get(
                                "livechat/department"
                            ).json()
                            department_triage_to_ignore = self.config.get(
                                "department_triage_to_ignore", []
                            ).split(",")
                            for department in departments.get("departments"):
                                if department.get("enabled"):
                                    if (
                                        department.get("_id")
                                        not in department_triage_to_ignore
                                    ):
                                        button = {
                                            "buttonId": department.get("_id"),
                                            "buttonText": {
                                                "displayText": department.get("name")
                                            },
                                            "type": 1,
                                        }
                                        buttons.append(button)
                            # the message is a button reply. we now register the room
                            # with the choosen department and return
                            if self.message.get("quotedMsg", {}).get(
                                "isDynamicReplyButtonsMsg", False
                            ):
                                # the department text is body
                                choosen_department = self.message.get("body")
                                department_map = {}
                                for b in buttons:
                                    department_map[b["buttonText"]["displayText"]] = b[
                                        "buttonId"
                                    ]
                                department = department_map[choosen_department]
                            else:
                                # add destination phone
                                payload = self.config.get("department_triage_payload")
                                payload["phone"] = self.get_visitor_id()
                                payload["buttons"] = buttons
                                # outcome buttons
                                message = {"msg": json.dumps(payload)}
                                self.outgo_text_message(message)
                                return JsonResponse(
                                    {
                                        "sucess": True,
                                        "message": "Departments triage button list sent",
                                    }
                                )
                    # get room
                    room = self.get_room(department)
                    #
                    # no room was generated
                    #
                    if not room:
                        return JsonResponse({"message": "no room generated"})

                    #
                    # type uknown
                    #
                    if self.message.get("type") == "unknown":
                        # in case it has message object attached
                        if not self.message_object.delivered:
                            self.message_object.delivered = True
                            self.message_object.save()
                        return JsonResponse({"message": "uknown type"})

                    #
                    # process different type of messages
                    #
                    # text type or dynamic button press
                    if self.message.get("type") == "chat" or self.message.get(
                        "quotedMsg", {}
                    ).get("isDynamicReplyButtonsMsg"):
                        # pre define the message to be delivered
                        message = self.get_message_body()
                        # Quoted Message in chat message
                        if self.message.get("quotedMsgId"):
                            quote_type = self.message.get("quotedMsg").get("type")
                            # type of message quoted is text
                            if quote_type == "chat":
                                quoted_body = self.message.get("quotedMsg").get("body")
                                if self.connector.config.get(
                                    "outcome_message_with_quoted_message", True
                                ):
                                    message = ":arrow_forward:  IN RESPONSE TO: {0} \n:envelope: {1}"
                                    message = message.format(
                                        quoted_body,
                                        self.get_message_body(),
                                    )
                            # type of message is others
                            elif quote_type in ["document", "image", "ptt"]:
                                message = "DOCUMENT RESENT:\n {0}".format(
                                    self.get_message_body()
                                )
                                mime = self.message.get("quotedMsg").get("mimetype")
                                # THE QUOTED BODY SEEMS CORRUPT
                                # body = self.message.get("quotedMsg").get("body")
                                # LETS GET THE ORIGINAL
                                # session = self.get_request_session()
                                # endpoint = "{0}/api/{1}/message-by-id/{2}".format(
                                #     self.config.get("endpoint"),
                                #     self.config.get("instance_name"),
                                #     self.message.get("quotedMsgId")
                                # )
                                # session = self.get_request_session()
                                # quoted_message = session.get(endpoint).json()
                                # file_to_send = self.message.get("quotedMsg").get("body")
                                # none of the above worked.
                                # will need to get original content from rocket.connect
                                try:
                                    quoted_message = self.connector.messages.get(
                                        envelope_id=self.message.get("quotedMsgId")
                                    )
                                    file_to_send = quoted_message.raw_message["body"]
                                except self.connector.messages.model.DoesNotExist:
                                    file_to_send = None
                                if file_to_send:
                                    file_sent = self.outcome_file(
                                        file_to_send,
                                        room.room_id,
                                        mime,
                                        description=self.message.get("quotedMsg").get(
                                            "caption", None
                                        ),
                                    )

                        # deliver text message
                        if room:
                            deliver = self.outcome_text(room.room_id, message)
                            if settings.DEBUG:
                                self.logger_info(
                                    "DELIVER OF TEXT MESSAGE: {0}".format(deliver.ok)
                                )
                    # location type
                    elif self.message.get("type") == "location":
                        lat = self.message.get("lat")
                        lng = self.message.get("lng")
                        link = "https://www.google.com/maps/search/?api=1&query={0}+{1}".format(
                            lat, lng
                        )
                        text = "Lat:{0}, Long:{1}: Link: {2}".format(
                            lat,
                            lng,
                            link,
                        )
                        self.outcome_text(
                            room.room_id, text, message_id=self.get_message_id()
                        )

                    # upload type
                    else:
                        if self.message.get("type") == "ptt":
                            self.handle_ptt()
                        # media type
                        mime = self.message.get("mimetype")
                        file_sent = self.outcome_file(
                            self.message.get("body"),
                            room.room_id,
                            mime,
                            description=self.message.get("caption", None),
                        )
                        if file_sent.ok:
                            self.message_object.delivered = True
                            self.message_object.save()
                else:
                    self.logger_info(
                        "Message Object {0} Already delivered. Ignoring".format(
                            message.id
                        )
                    )

        # unread messages - just logging
        if self.message.get("event") == "unreadmessages":
            if "status@broadcast" not in self.message.get(
                "from"
            ) and not self.message.get("id", {}).get("fromMe", False):
                self.logger_info(
                    "PROCESSED UNREAD MESSAGE. PAYLOAD {0}".format(self.message)
                )

        # webhook active chat integration
        if self.config.get("active_chat_webhook_integration_token"):
            if self.message.get("token") == self.config.get(
                "active_chat_webhook_integration_token"
            ):
                self.logger_info("active_chat_webhook_integration_token triggered")
                # message, created = self.register_message()
                req = self.active_chat()
                return JsonResponse(req)

        return JsonResponse({})

    def intake_unread_messages(self):
        """
        intake unread messages
        """
        endpoint = "{0}/api/{1}/unread-messages".format(
            self.config.get("endpoint"),
            self.config.get("instance_name"),
        )
        session = self.get_request_session()
        unread_contacts = session.get(endpoint)
        if unread_contacts.ok:
            self.logger_error("PROCESSING UNREAD CONTACTS ON START")
            unread_contacts = unread_contacts.json()
            for contact in unread_contacts.get("response"):
                for message in contact["messages"]:
                    message["event"] = "onmessage"
                    message["chatId"] = message["from"]
                    self.logger_error("PROCESSING UNREAD MESSAGE {0}".format(message))
                    self.message = message
                    self.type = "incoming"
                    self.incoming()
        else:
            self.logger_error("COULD NOT CONNECT TO WPP SERVER TO GET UNREAD MESSAGES")

        return False

    def get_incoming_message_id(self):
        # unread messages has a different structure
        if self.message.get("event") == "unreadmessages":
            return self.message.get("id", {}).get("_serialized")
        if self.message.get("type") == "active_chat":
            return self.message.get("message_id")
        return self.message.get("id")

    def get_incoming_visitor_id(self):
        if self.message.get("event") == "incomingcall":
            return self.message.get("peerJid")
        else:
            if self.message.get("event") == "unreadmessages":
                return self.message.get("from")
            else:
                return self.message.get("chatId")

    def get_visitor_name(self):
        # get name order
        name_order = self.config.get("name_extraction_order", "pushname,name,shortName")
        message = self.message
        order = name_order.split(",")
        name = None
        # try each attribute
        for attribute in order:
            if not name:
                name = message.get("sender", {}).get(attribute)
        # get the fallback name
        if not name:
            name = message.get("chatId")
        return name

    def get_visitor_phone(self):
        if self.message.get("event") == "incomingcall":
            visitor_phone = self.message.get("peerJid").split("@")[0]
        else:
            visitor_phone = self.message.get("from").split("@")[0]
        return visitor_phone

    def get_visitor_username(self):
        if self.message.get("event") == "incomingcall":
            visitor_username = "whatsapp:{0}".format(
                # works for wa-automate
                self.message.get("peerJid")
            )
        else:
            visitor_username = "whatsapp:{0}".format(self.message.get("from"))
        return visitor_username

    def get_message_body(self):
        return self.message.get("body")

    def get_request_session(self):
        s = requests.Session()
        s.headers = {"content-type": "application/json"}
        token = self.connector.config.get("token", {}).get("token")
        if token:
            s.headers.update({"Authorization": "Bearer {0}".format(token)})
        return s

    def outgo_text_message(self, message, agent_name=None):
        sent = False
        content = message["msg"]
        try:
            # mesangem é um json
            payload = json.loads(content)
            if payload.get("buttons"):
                if not payload.get("phone"):
                    payload["phone"] = self.get_visitor_id()
                url = self.connector.config[
                    "endpoint"
                ] + "/api/{0}/send-buttons".format(
                    self.connector.config["instance_name"]
                )
                self.logger_info(
                    "OUTGOING BUTTON MESSAGE. URL: {0}. PAYLOAD {1}".format(
                        url, payload
                    )
                )
        except ValueError:
            content = self.joypixel_to_unicode(content)
            # message may not have an agent
            if agent_name:
                content = "*[" + agent_name + "]*\n" + content

            payload = {
                "phone": self.get_visitor_id(),
                "message": content,
                "isGroup": False,
            }
            url = self.connector.config["endpoint"] + "/api/{0}/send-message".format(
                self.connector.config["instance_name"]
            )
            self.logger_info(
                "OUTGOING TEXT MESSAGE. URL: {0}. PAYLOAD {1}".format(url, payload)
            )
        # SEND MESSAGE
        session = self.get_request_session()
        # TODO: Simulate typing
        # See: https://github.com/wppconnect-team/wppconnect-server/issues/59

        timestamp = int(time.time())
        try:
            sent = session.post(url, json=payload)
            if self.message_object:
                self.message_object.delivered = sent.ok
                self.message_object.response[timestamp] = sent.json()
        except requests.ConnectionError:
            if self.message_object:
                self.message_object.delivered = False
                self.logger_info("CONNECTOR DOWN: {0}".format(self.connector))
        # save message object
        if self.message_object:
            self.message_object.payload[timestamp] = payload
            self.message_object.save()
        return sent

    def outgo_file_message(self, message, agent_name=None):
        # if its audio, treat different
        # ppt = False
        # if message["file"]["type"] == "audio/mpeg":
        #     ppt = True

        # to avoid some networking problems,
        # we use the same url as the configured one, as some times
        # the url to get the uploaded file may be different
        # eg: the publicFilePath is public, but waautomate is running inside
        # docker
        file_url = (
            self.connector.server.url
            + message["attachments"][0]["title_link"]
            + "?"
            + urlparse.urlparse(message["fileUpload"]["publicFilePath"]).query
        )
        content = base64.b64encode(requests.get(file_url).content).decode("utf-8")
        mime = self.message["messages"][0]["fileUpload"]["type"]
        payload = {
            "phone": self.get_visitor_id(),
            "base64": "data:{0};base64,{1}".format(mime, content),
            "isGroup": False,
        }
        if settings.DEBUG:
            print("PAYLOAD OUTGOING FILE: ", payload)
        session = self.get_request_session()
        url = self.connector.config["endpoint"] + "/api/{0}/send-file-base64".format(
            self.connector.config["instance_name"]
        )
        sent = session.post(url, json=payload)
        if sent.ok:
            timestamp = int(time.time())
            if settings.DEBUG:
                print("RESPONSE OUTGOING FILE: ", sent.json())
            self.message_object.payload[timestamp] = payload
            self.message_object.delivered = True
            self.message_object.response[timestamp] = sent.json()
            self.message_object.save()
            # self.send_seen()

    def outgo_vcard(self, payload):
        session = self.get_request_session()
        url = self.connector.config["endpoint"] + "/api/{0}/contact-vcard".format(
            self.connector.config["instance_name"]
        )
        self.logger_info("OUTGOING VCARD. URL: {0}. PAYLOAD {1}".format(url, payload))
        timestamp = int(time.time())
        try:
            # replace destination phone
            payload["phone"] = self.get_visitor_phone()
            sent = session.post(url, json=payload)
            self.message_object.delivered = sent.ok
            self.message_object.response[timestamp] = sent.json()
        except requests.ConnectionError:
            self.message_object.delivered = False
            self.logger_info("CONNECTOR DOWN: {0}".format(self.connector))
        # save message object
        if self.message_object:
            self.message_object.payload[timestamp] = payload
            self.message_object.save()


class ConnectorConfigForm(BaseConnectorConfigForm):

    webhook = forms.CharField(
        help_text="Where WPPConnect will send the events",
        required=True,
    )
    endpoint = forms.CharField(
        help_text="Where your WPPConnect is installed",
        required=True,
        initial="http://wppconnect:21465",
    )
    secret_key = forms.CharField(
        help_text="The secret key for your WPPConnect instance",
        required=True,
    )
    instance_name = forms.CharField(
        help_text="WPPConnect instance name", validators=[validators.validate_slug]
    )

    active_chat_webhook_integration_token = forms.CharField(
        required=False,
        help_text="Put here the same token used for the active chat integration",
        validators=[validators.validate_slug],
    )

    name_extraction_order = forms.CharField(
        required=False,
        help_text="The prefered order to extract a visitor name",
        initial="name,shortName,pushname",
    )

    process_unread_messages_on_start = forms.BooleanField(initial=False, required=False)

    department_triage = forms.BooleanField(required=False)

    department_triage_payload = forms.JSONField(required=False)

    department_triage_to_ignore = forms.CharField(max_length=None, required=False)

    outcome_message_with_quoted_message = forms.BooleanField(required=False)

    field_order = [
        "webhook",
        "endpoint",
        "secret_key",
        "instance_name",
        "active_chat_webhook_integration_token",
        "name_extraction_order",
        "process_unread_messages_on_start",
        "outcome_message_with_quoted_message",
        "department_triage",
        "department_triage_payload",
        "department_triage_to_ignore",
    ]
