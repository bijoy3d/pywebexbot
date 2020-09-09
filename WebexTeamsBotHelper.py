import threading
import asyncio
import six
import time
import copyreg
import json
import sys
import logging
import uuid
import websockets
import string
import random
import os
from markdown import markdown
import websocket
from errbot.core import ErrBot
from errbot.backends.base import Message, Person, Room, RoomOccupant, OFFLINE, RoomDoesNotExistError, Stream
from errbot import rendering
from threading import Thread

import webexteamssdk

__version__ = "1.6.0"

log = logging.getLogger('errbot.backends.CiscoWebexTeams')
logging.basicConfig(filename="botbackendlog") 

CISCO_WEBEX_TEAMS_MESSAGE_SIZE_LIMIT = 7439

DEVICES_URL = 'https://wdm-a.wbx2.com/wdm/api/v1/devices'

DEVICE_DATA = {
    "deviceName"    : "pywebsocket-client",
    "deviceType"    : "DESKTOP",
    "localizedModel": "python",
    "model"         : "python",
    "name"          : f"python-webex-teams-client-{''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(5))}",
    "systemName"    : "python-webex-teams-client",
    "systemVersion" : "0.1"
}


class FailedToCreateWebexDevice(Exception):
    pass


class FailedToFindWebexTeamsPerson(Exception):
    pass


class FailedToFindWebexTeamsRoom(Exception):
    pass


class CiscoWebexTeamsMessage(Message):
    """
    A Cisco Webex Teams Message
    """
    @property
    def is_direct(self) -> bool:
        return self.extras['roomType'] == 'direct'

    @property
    def is_group(self) -> bool:
        return not self.is_direct


class CiscoWebexTeamsPerson(Person):
    """
    A Cisco Webex Teams Person
    """
    def __init__(self, backend, attributes=None):

        self._backend = backend
        attributes = attributes or {}

        if isinstance(attributes, webexteamssdk.Person):
            self.teams_person = attributes
        else:
            self.teams_person = webexteamssdk.Person(attributes)

    @property
    def id(self):
        return self.teams_person.id

    @id.setter
    def id(self, val):
        self.teams_person._json_data['id'] = val

    @property
    def emails(self):
        return self.teams_person.emails

    @emails.setter
    def emails(self, val):
        self.teams_person._json_data['emails'] = val

    @property
    def email(self):
      if type(self.emails) is list:
        if len(self.emails):
          # Note sure why a person can have multiple email addresses
          return self.emails[0]

      return None

    @email.setter
    def email(self, val):
      self.emails = [val]

    @property
    def aclattr(self):
        return self.teams_person.email

    @property
    def displayName(self):
        return self.teams_person.displayName

    @property
    def created(self):
        return self.teams_person.created

    @property
    def avatar(self):
        return self.teams_person.avatar

    def find_using_email(self):
        """
        Return the FIRST Cisco Webex Teams person found when searching using an email address
        """
        try:
            for person in self._backend.webex_teams_api.people.list(email=self.email):
                self.teams_person = person
                return
        except:
            raise FailedToFindWebexTeamsPerson(f'Could not find a user using the email address {self.email}')

    def find_using_name(self):
        """
        Return the FIRST Cisco Webex Teams person found when searching using the display name
        """
        try:
            for person in self._backend.webex_teams_api.people.list(displayName=self.displayName):
                self.teams_person = person
                return
        except:
            raise FailedToFindWebexTeamsPerson(f'Could not find the user using the displayName {self.displayName}')

    def get_using_id(self):
        """
        Return a Cisco Webex Teams person when searching using an ID
        """
        try:
            self._backend.webex_teams_api.people.get(self.id)
        except:
          raise FailedToFindWebexTeamsPerson(f'Could not find the user using the id {self.id}')

    # Required by the Err API

    @property
    def person(self):
        return self.email

    @property
    def client(self):
        return ''

    @property
    def nick(self):
        return ''

    @property
    def fullname(self):
        return self.displayName

    def json(self):
        return self.teams_person.json()

    def __eq__(self, other):
        return str(self) == str(other)

    def __unicode__(self):
        return self.email

    __str__ = __unicode__


class CiscoWebexTeamsRoomOccupant(CiscoWebexTeamsPerson, RoomOccupant):
    """
    A Cisco Webex Teams Person that Occupies a Cisco Webex Teams Room
    """
    def __init__(self, backend, room=None, person=None):

        room = room or {}
        person = person or {}

        if isinstance(room, CiscoWebexTeamsRoom):
            self._room = room
        else:
            self._room = CiscoWebexTeamsRoom(backend=backend, room_id=room)

        if isinstance(person, CiscoWebexTeamsPerson):
            self.teams_person = person
        else:
            self.teams_person = CiscoWebexTeamsPerson(backend=backend, attributes=person)

    @property
    def room(self):
        return self._room


class CiscoWebexTeamsRoom(Room):
    """
    A Cisco Webex Teams Room
    """
    def __init__(self, backend, room_id=None, room_title=None):

        self._backend = backend
        self._room_id = room_id
        self._room_title = room_title
        self._room = None

        if room_id is not None and room_title is not None:
            raise ValueError("room_id and room_title are mutually exclusive")

        if not room_id and not room_title:
            raise ValueError("room_id or room_title is needed")

        if room_title is not None:
            self.load_room_from_title()
        else:
            self.load_room_from_id()

    def load_room_from_title(self):
        """
        Load a room object from a title. If no room is found, return a new Room object.
        """
        rooms = self._backend.webex_teams_api.rooms.list()
        room = [room for room in rooms if room.title == self._room_title]

        if not len(room) > 0:
            self._room = webexteamssdk.models.immutable.Room({})
            self._room_id = None
        else:
            # TODO: not sure room title will duplicate
            self._room = room[0]
            self._room_id = self._room.id

    def load_room_from_id(self):
        """
        Load a room object from a webex room id. If no room is found, return a new Room object.
        """
        try:
            self._room = self._backend.webex_teams_api.rooms.get(self._room_id)
            self._room_title = self._room.title
        except webexteamssdk.exceptions.ApiError:
            self._room = webexteamssdk.models.immutable.Room({})

    @property
    def id(self):
        """Return the ID of this room"""
        return self._room_id

    @property
    def room(self):
        """Return the webexteamssdk.models.immutable.Room instance"""
        return self._room

    @property
    def created(self):
        return self._room.created

    @property
    def title(self):
        return self._room_title

    # Errbot API

    def join(self, username=None, password=None):

        log.debug(f'Joining room {self.title} ({self.id})')
        try:
            self._backend.webex_teams_api.memberships.create(self.id, self._backend.bot_identifier.id)
            log.debug(f'{self._backend.bot_identifier.displayName} is NOW a member of {self.title} ({self.id}')

        except webexteamssdk.exceptions.ApiError as error:
            # API now returning a 403 when trying to add user to a direct conversation and they are already in the
            # conversation. For groups if the user is already a member a 409 is returned.
            if error.response.status_code == 403 or error.response.status_code == 409:
                log.debug(f'{self._backend.bot_identifier.displayName} is already a member of {self.title} ({self.id})')
            else:
                log.exception(f'HTTP Exception: Failed to join room {self.title} ({self.id})')
                return

        except Exception:
            log.exception("Failed to join room {} ({})".format(self.title, self.id))
            return

    def leave(self, reason=None):
        log.debug("Leave room yet to be implemented")  # TODO
        pass

    def create(self):
        """
        Create a new room. Membership to the room is provide by default.
        """
        self._room = self._backend.webex_teams_api.rooms.create(self.title)
        self._room_id = self._room.id
        self._backend.webex_teams_api.messages.create(roomId=self._room_id, text="Welcome to the room!")
        log.debug(f'Created room: {self.title}')

    def destroy(self):
        """
        Destroy (delete) a room
        :return:
        """
        self._backend.webex_teams_api.rooms.delete(self.id)
        # We want to re-init this room so that is accurately reflects that is no longer exists
        self.load_room_from_title()
        log.debug(f'Deleted room: {self.title}')

    @property
    def exists(self):
        return not self._room.created == None

    @property
    def joined(self):
        rooms = self._backend.webex_teams_api.rooms.list()
        return len([room for room in rooms if room.title == room.title]) > 0

    @property
    def topic(self):
        return self.title

    @topic.setter
    def topic(self, topic):
        log.debug("Topic room yet to be implemented")  # TODO
        pass

    @property
    def occupants(self):

        if not self.exists:
            raise RoomDoesNotExistError(f"Room {self.title or self.id} does not exist, or the bot does not have access")

        occupants = []

        for person in self._backend.webex_teams_api.memberships.list(roomId=self.id):
            p = CiscoWebexTeamsPerson(backend=self._backend)
            p.id = person.personId
            p.email = person.personEmail
            occupants.append(CiscoWebexTeamsRoomOccupant(backend=self._backend, room=self, person=p))

        log.debug("Total occupants for room {} ({}) is {} ".format(self.title, self.id, len(occupants)))

        return occupants

    def invite(self, *args):
        log.debug("Invite room yet to be implemented")  # TODO
        pass

    def __eq__(self, other):
        return str(self) == str(other)

    def __unicode__(self):
        return self.title

    __str__ = __unicode__


class CiscoWebexTeamsBackend(ErrBot):
    """
    This is the CiscoWebexTeams backend for errbot.
    """

    wsk = None
    def __init__(self, token):

        bot_identity = BOT_IDENTITY = {
            'TOKEN': token,
        }
        self.md = rendering.md()

        # Do we have the basic mandatory config needed to operate the bot
        self._bot_token = bot_identity.get('TOKEN', None)
        if not self._bot_token:
            log.fatal('You need to define the Cisco Webex Teams Bot TOKEN in the BOT_IDENTITY of config.py.')
            sys.exit(1)

        print("Setting up SparkAPI")
        self.webex_teams_api = webexteamssdk.WebexTeamsAPI(access_token=self._bot_token)

        print("Setting up device on Webex Teams")
        self.device_info = self._get_device_info()

        print("Fetching and building identifier for the bot itself.")
        self.bot_identifier = CiscoWebexTeamsPerson(self, self.webex_teams_api.people.me())

        print("Done! I'm connected as {}".format(self.bot_identifier.email))

        self._register_identifiers_pickling()

    @property
    def mode(self):
        return 'CiscoWebexTeams'

    def is_from_self(self, message):
      return message.frm.id == message.to.id

    def process_websocket(self, message):
        """
        Process the data from the websocket and determine if we need to ack on it
        :param message: The message received from the websocket
        :return:
        """
        message = json.loads(message.decode('utf-8'))
        if message['data']['eventType'] != 'conversation.activity':
            logging.debug('Ignoring message where Event Type is not conversation.activity')
            return

        activity = message['data']['activity']

        if activity['verb'] != 'post':
            logging.debug('Ignoring message where the verb is not type "post"')
            return

        spark_message = self.webex_teams_api.messages.get(activity['id'])

        if spark_message.personEmail in self.bot_identifier.emails:
            logging.debug('Ignoring message from myself')
            return

        logging.info('Message from %s: %s\n' % (spark_message.personEmail, spark_message.text))
        self.callback_message(self.get_message(spark_message))

    def get_message(self, message):
        """
        Create an errbot message object
        """
        person = CiscoWebexTeamsPerson(self)
        person.id = message.id
        person.email = message.personEmail
        try:
            parentId = message.parentId
        except AttributeError:
            parentId = message.id

        room = CiscoWebexTeamsRoom(backend=self, room_id=message.roomId)
        occupant = CiscoWebexTeamsRoomOccupant(self, person=person, room=room)
        msg = CiscoWebexTeamsMessage(body=message.markdown or message.text,
                                     frm=occupant,
                                     to=room,
                                     extras={'roomType': message.roomType,'parentId': parentId})
        return msg

    def follow_room(self, room):
        """
        Backend: Follow Room yet to be implemented

        :param room:
        :return:
        """
        log.debug("Backend: Follow Room yet to be implemented")  # TODO
        pass

    def rooms(self):
        """
        Backend: Rooms that the bot is a member of

        :return:
            List of rooms
        """
        return [f"{room.title} ({room.type})" for room in self.webex_teams_api.rooms.list()]

    def contacts(self):
        """
        Backend: Contacts yet to be implemented

        :return:
        """
        log.debug("Backend: Contacts yet to be implemented")  # TODO
        pass

    def build_identifier(self, strrep):
        """
        Build an errbot identifier using the Webex Teams email address of the person

        :param strrep: The email address of the Cisco Webex Teams person
        :return: CiscoWebexTeamsPerson
        """
        person = CiscoWebexTeamsPerson(self)
        person.email = strrep
        person.find_using_email()
        return person

    def query_room(self, room_id_or_name):
        """
        Create a CiscoWebexTeamsRoom object identified by the ID or name of the room

        :param room_id_or_name:
            The Cisco Webex Teams room ID or a room name
        :return:
            :class: CiscoWebexTeamsRoom
        """
        if isinstance(room_id_or_name, webexteamssdk.Room):
            return CiscoWebexTeamsRoom(backend=self, room_id=room_id_or_name.id)

        # query_room can provide us either a room name of an ID, so we need to check
        # for both
        room = CiscoWebexTeamsRoom(backend=self, room_id=room_id_or_name)

        if not room.exists:
            room = CiscoWebexTeamsRoom(backend=self, room_title=room_id_or_name)

        return room

    def send_card(self, card):
        """Send a card out to Webex Teams."""

        # Need to strip out "markdown extra" as not supported by Webex Teams
        md = markdown(self.md.convert(card.body),
                      extensions=['markdown.extensions.nl2br',
                                  'markdown.extensions.fenced_code'])

        payload = {
            "text": card.body,
            "markdown": md,
        }
        if hasattr(card.parent, "extras"):
            payload["parentId"] = card.parent.extras['parentId']

        if hasattr(card, "layout"):
            payload["attachments"] = [card.layout]

        if type(card.to) == CiscoWebexTeamsPerson:
            payload["toPersonId"] = card.to.id
        else:
            payload["roomId"] = card.to.room.id

        self.webex_teams_api.messages.create(**payload)

    def send_message(self):#, mess):
        """
        Send a message to Cisco Webex Teams

        :param mess: A CiscoWebexTeamsMessage
        """
        roomid = 'Y2lzY29zcGFyazovL3VzL1JPT00vNGYyYzM4NzAtY2FkZS0xMWVhLTgyODEtNmI3NTgwZGU1ZDM2'
        # Need to strip out "markdown extra" as not supported by Webex Teams
#        md = markdown(self.md.convert(mess.body),
 #                     extensions=['markdown.extensions.nl2br', 'markdown.extensions.fenced_code'])

  #      if type(mess.to) == CiscoWebexTeamsPerson:
   #         self.webex_teams_api.messages.create(toPersonId=mess.to.id, text=mess.body, markdown=md)
    #    else:
        #if mess.parent is not None:
         #  self.webex_teams_api.messages.create(roomId=mess.to.room.id, text=mess.body, markdown=md, parentId=mess.parent.extras['parentId'])
        #else:
        self.webex_teams_api.messages.create(roomId=roomid, text="TEST")#, markdown=md)

    def _teams_upload(self, stream):
        """
        Performs an upload defined in a stream
        :param stream: Stream object
        :return: None
        """

        try:
            stream.accept()
            log.exception(f'Upload of {stream.raw.name} to {stream.identifier} has started.')

            if type(stream.identifier) == CiscoWebexTeamsPerson:
                self.webex_teams_api.messages.create(toPersonId=stream.identifier.id, files=[stream.raw.name])
            else:
                self.webex_teams_api.messages.create(roomId=stream.identifier.room.id, files=[stream.raw.name])

            stream.success()
            log.exception(f'Upload of {stream.raw.name} to {stream.identifier} has completed.')

        except Exception:
            stream.error()
            log.exception(f'Upload of {stream.raw.name} to {stream.identifier} has failed.')

        finally:
            stream.close()

    def send_stream_request(self, identifier, fsource, name='file', size=None, stream_type=None):
        """
        Send a file to Cisco Webex Teams

        :param user: is the identifier of the person you want to send it to.
        :param fsource: is a file object you want to send.
        :param name: is an optional filename for it.
        :param size: not supported in Webex Teams backend
        :param stream_type: not supported in Webex Teams backend
        """
        log.debug(f'Requesting upload of {fsource.name} to {identifier}.')
        stream = Stream(identifier, fsource, name, size, stream_type)
#        self.thread_pool.apply_async(self._teams_upload, (stream,))
        self._teams_upload(stream)
        return stream

    def build_reply(self, mess, text=None, private=False, threaded=False):
        """
        Build a reply in the format expected by errbot by swapping the to and from source and destination

        :param mess: The original CiscoWebexTeamsMessage object that will be replied to
        :param text: The text that is to be sent in reply to the message
        :param private: Boolean indiciating whether the message should be directed as a private message in lieu of
                        sending it back to the room
        :return: CiscoWebexTeamsMessage
        """
        response = self.build_message(text)
        response.frm = mess.to
        response.to = mess.frm
        return response

    def disconnect_callback(self):
        """
        Disconnection has been requested, lets make sure we clean up
        """
        super().disconnect_callback()

    def on_msg(self):
        print("Message Received")

    def on_err(self):
        print("ERROR")

    def on_open(self):
        print("OPENED")

    def on_close(self):
        print("CLOSING.....")

    async def serve_once(self):
        """
        Signal that we are connected to the Webex Teams Service and hang around waiting for disconnection request
        """
        try:
            url = self.device_info['webSocketUrl']
            print("Opening websocket connection to %s" % websocket)
            websocket.enableTrace(True)
            #ws = websocket.create_connection(url)
            #in_data = ws.recv()
            #print(in_data)
            async with websocket.WebSocketApp(url,on_message=self.on_msg,on_error=self.on_err,on_close=self.on_close) as self.wsk:
                self.wsk.on_open = self.on_open
                print("Ging to RUN forever")
            #    self.wsk.run_forever(http_proxy_host="proxy-wsa.esl.cisco.com", http_proxy_port=80)
        except KeyboardInterrupt:
            print("Interrupt received, shutting down..")
            return True

    def _get_device_info(self):
        """
        Setup device in Webex Teams to bridge events across websocket
        :return:
        """
        logging.debug('Getting device list from Webex Teams')

        try:
            print(DEVICES_URL)
            resp = self.webex_teams_api._session.get(DEVICES_URL)
            for device in resp['devices']:
                print(device['name'])
                if device['name'] == DEVICE_DATA['name']:
                    self.device_info = device
                    return device
                else:
                    resp = self.webex_teams_api._session.delete(DEVICES_URL)
                    print(resp)

        except webexteamssdk.ApiError:
            pass

        print('Device does not exist in Webex Teams, creating')

        resp = self.webex_teams_api._session.post(DEVICES_URL, json=DEVICE_DATA)
        if resp is None:
            raise FailedToCreateWebexDevice("Could not create Webex Teams device using {}".format(DEVICES_URL))

        self.device_info = resp
        return resp

    def change_presence(self, status=OFFLINE, message=''):
        """
        Backend: Change presence yet to be implemented

        :param status:
        :param message:
        :return:
        """
        log.debug("Backend: Change presence yet to be implemented")  # TODO
        pass

    def prefix_groupchat_reply(self, message, identifier):
        """
        Backend: Prefix group chat reply yet to be implemented

        :param message:
        :param identifier:
        :return:
        """
        log.debug("Backend: Prefix group chat reply yet to be implemented")  # TODO
        pass

    def remember(self, id, key, value):
        """
        Save the value of a key to a dictionary specific to a Webex Teams room or person
        This is available in backend to provide easy access to variables that can be shared between plugins

        :param id: Webex Teams ID of room or person
        :param key: The dictionary key
        :param value:  The value to be assigned to the key
        """
        values = self.recall(id)
        values[key] = value
        self[id] = values

    def forget(self, id, key):
        """
        Delete a key from a dictionary specific to a Webex Teams room or person

        :param id: Webex Teams ID of room or person
        :param key: The dictionary key
        :return: The popped value or None if the key was not found
        """
        values = self.recall(id)
        value = values.pop(key, None)
        self[id] = values
        return value

    def recall(self, id):
        """
        Access a dictionary for a room or person using the Webex Teams ID as the key

        :param id: Webex Teams ID of room or person
        :return: A dictionary. If no dictionary was found an empty dictionary will be returned.
        """
        values = self.get(id)
        return values if values else {}

    def recall_key(self, id, key):
        """
        Access the value of a specific key from a Webex Teams room or person dictionary

        :param id: Webex Teams ID of room or person
        :param key: The dictionary key
        :return: Either the value of the key or None if the key is not found
        """
        return self.recall(id).get(key)

    @staticmethod
    def _unpickle_identifier(identifier_str):
        return CiscoWebexTeamsBackend.__build_identifier(identifier_str)

    @staticmethod
    def _pickle_identifier(identifier):
        return CiscoWebexTeamsBackend._unpickle_identifier, (str(identifier),)

    def _register_identifiers_pickling(self):
        """
        Register identifiers pickling.
        """
        CiscoWebexTeamsBackend.__build_identifier = self.build_identifier
        for cls in (CiscoWebexTeamsPerson, CiscoWebexTeamsRoomOccupant, CiscoWebexTeamsRoom):
            copyreg.pickle(cls, CiscoWebexTeamsBackend._pickle_identifier, CiscoWebexTeamsBackend._unpickle_identifier)

    async def __aexit__(self, exc_type, exc, tb):
        print("EXITING")

class FireBot():

    commands={}
    token=""
    bot = None
    def __init__(self, token):
        self.token = token
        self.commands["help"]=[self.helpme, "List all commands"]
        print(token)

    def helpme(self, *argv):
        msg = argv[0]
        helpdoc = "Here are the available commands :\n"
        for cmd,helper in self.commands.items():
            if cmd!="cardaction":
                helpdoc+='{} : {}'.format(cmd, helper[1])+"\n"
        self.send_message(msg.roomId, helpdoc)

    def add_command(self, command, func, helper=None):
        if command is None or func is None:
            return 0
        else:
            print("ADDING COMMAND")
            if isinstance(func, str):
                func = eval(func)
            self.commands[command.lower()]=[func, helper]
            print(self.commands)

    def process_command(self, txt, msg):
        print(txt)
        if txt in self.commands:
            return self.commands[txt][0]
        else:
            return self.commands["help"][0](msg)

    def process_card_action(self):
        return self.commands["cardaction"][0]

    def start_bot(self):
        """
        Signal that we are connected to the Webex Teams Service.
        Hang around waiting for disconnection request
        """
        try:
            bot = self.bot
            print(bot.bot_identifier.displayName)
            url = bot.device_info['webSocketUrl']
            print("Opening websocket connection to %s" % websocket)
            websocket.enableTrace(True)
            print("Creating Connection")
            wsk = websocket.create_connection(url)
            print(wsk)
            bot.wsk=wsk
            print('Connection Created')
            bot.wsk.on_open = bot.on_open
            msg = {'id': str(uuid.uuid4()),
                   'type': 'authorization',
                   'data': {
                            'token': 'Bearer ' + bot._bot_token
                           }
                  }
            bot.wsk.send(json.dumps(msg))
        
            while True:
                in_data = bot.wsk.recv()
                message = json.loads(in_data.decode('utf-8'))
                if message['data']['eventType'] != 'conversation.activity':
                    print('Ignoring msg where Event Type is not conversation.activity')
                    continue 
                print(message) 
                activity = message['data']['activity']
                #print(activity)
                if activity['verb'] == 'cardAction':
                    msg = bot.webex_teams_api.attachment_actions.get(activity['id'])
                    pmsg =bot.webex_teams_api.messages.get(activity['parent']['id']) 
                    cmd = self.process_card_action()
                    x=threading.Thread(target=cmd, args=(msg,pmsg,activity,))
                    x.start()
                    continue
                if activity['verb'] != 'post':
                    print('Ignoring message where the verb is not type "post"')
                    continue 
                teams_msg = bot.webex_teams_api.messages.get(activity['id'])
                if teams_msg.personEmail in bot.bot_identifier.emails:
                    print('Ignoring message from myself')
                    continue 
                txt = teams_msg.text
                botName = bot.bot_identifier.displayName
                print('Message from %s: %s\n' % (teams_msg.personEmail,txt))
                if txt.find(botName) != -1:
                    txt = (txt[len(botName):]).strip()
                elif txt.find(botName.split(" ")[0]) != -1:
                    txt = (txt[len(botName.split(" ")[0]):]).strip()
                txt = ((txt.lower()).replace(" ", "")).strip()
                if txt in self.commands:
                    print("CMD FOUND")
                    command = self.process_command(txt, teams_msg)
                    print(command)
                    x=threading.Thread(target=command, args=(teams_msg,activity))
                    x.start()
        except KeyboardInterrupt:
            print("Interrupt received, shutting down..")
            bot.wsk.shutdown()
            return True

    def get_using_id(self, pid):
        """
        Return a Cisco Webex Teams person when searching using an ID
        """
        try:
            print(pid)
            self.webex_teams_api.people.get(pid)
        except:
          raise FailedToFindWebexTeamsPerson(f'Could not find the user using the id {pid}')
    def delete_message(self, mid):
        self.bot.webex_teams_api.messages.delete(mid)

    def send_message(self, roomId, mess, parent=None):
        """
        Send a message to Cisco Webex Teams

        :param mess: A CiscoWebexTeamsMessage
        """
 #       if parent is not None:
        return self.bot.webex_teams_api.messages.create(roomId=roomId, text=mess, parentId=parent)
#        else:
 #           return self.bot.webex_teams_api.messages.create(roomId=roomid, text=mess)#, markdown=md)

    def send_file(self, rid, filen, filel):
        self.bot.webex_teams_api.messages.create(roomId=rid, files=[filel,])

    def send_message_with_attachment(self, rid, msgtxt, attachment):
        if isinstance(attachment, str):
            attachment=json.loads(attachment)
        alist = [attachment]
        return(self.bot.webex_teams_api.messages.create(roomId=rid, markdown=msgtxt, attachments=alist))

    def botwrap(self):
        print('botwrap')
        while True:
            try:
               self.start_bot()
            except BaseException as e:
               print('{!r}; restarting thread'.format(e))
            else:
               print('exited normally, bad thread; restarting')

    def start(self):
        bot = CiscoWebexTeamsBackend(self.token)
        print(bot.rooms())
        self.bot=bot
        x=threading.Thread(target=self.botwrap, args=())
        x.start()
