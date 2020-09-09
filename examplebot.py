# -*- coding: utf-8 -*-
"""
EXAMPLE BOT
"""
import os
import requests
import sys
import json
import threading
import logging
import WebexTeamsBotHelper

log = print

class myBot():
    test = 1 
    bot=None
    attachment = "PUT YOUR CARD JSON HERE"

    def pincoming(self, incoming_msg):
        log("INCOMING-INCOMING-INCOMING-INCOMING-INCOMING-INCOMING-INCOMING-INCOMING")
        log(str(incoming_msg))
        log("INCOMING-INCOMING-INCOMING-INCOMING-INCOMING-INCOMING-INCOMING-INCOMING")

    def __init__(self, bot):
        bot.add_command("command", self.command, "Command HELP")
        bot.add_command("cardcommand", self.card_command, "Card Command Example")
        bot.add_command("cardAction", self.handle_cards, "")
        self.bot = bot

    def start(self):
        self.bot.start()

    def command(self, msg):
        self.bot.send_message(msg.roomId, "HELLO")

    def card_command(self, msg):
        attach = ""
        ## Create JSON here : https://developer.webex.com/buttons-and-cards-designer
        with open('path_to_file/json.json') as f:
            attach = json.load(f)

        self.bot.send_message_with_attachment(msg.roomId, msgtxt="Card Example", attachment = attach)


    def handle_cards(self, msg, pmsg, activity):
        ##HANDLE CARD ACTIONS HERE
        self.pincoming(msg)

def main():
    token = 'PASTE_TOKEN_HERE'
    botobj = WebexTeamsBotHelper.FireBot(token)
    mybot = myBot(botobj)
    mybot.start() 

    while True:
        print("Call any function here. Bot deciding to send messages based on server actions")

if __name__ == "__main__":
    main()
