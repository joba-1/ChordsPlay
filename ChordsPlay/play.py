'''
Created on 06.03.2016

@author: joba1
'''

import vlc
import json
import subprocess
import re
import os
import logging

log = logging.getLogger(__name__)

class Play(object):
    '''
    play songs
    '''
    def __init__(self, widget, db):
        '''
        init player (currently a python wrapper of libvlc)
        '''
        # VLC player controls
        self.instance = vlc.Instance()
        
        self.player = self.instance.media_player_new()

        self.tk = widget        
        self.event_manager = self.player.event_manager()
        self.event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self.onEnd)

        self.url = None
        self.song_id = None        
        self.db = db
        self.module = None
        self.device = None
        self.sinks = None
        self.stream = None
        
        log.info("init: aflg: {}".format(json.dumps(self.instance.audio_filter_list_get(), ensure_ascii=False, sort_keys=True, indent=2, default=str)))
        log.info("init: aolg: {}".format(json.dumps(self.getAudioOut(), ensure_ascii=False, sort_keys=True, indent=2, default=str)))
    
    def getPulseInfo(self):
        sinks = []
        stream = None
        pattern = re.compile(r'\s*([^#:=]*?)\s*([#:=])\s*(.*)')
        portPattern = re.compile(r'([^\(]*?)\s*\((.*)')
        env = os.environ
        env['LANG'] = 'C'
        try:
            output = subprocess.check_output(["pactl", "list"], env=env, universal_newlines=True)
        except:
            output = ''
        sink = None
        inp = None
        ports = None
        for line in output.splitlines():
            match = pattern.match(line)
            if match != None:
                key, sep, value = match.groups()
                log.debug("getPulseInfo: match key '{}' sep '{}' value '{}'".format(key, sep, value))
                if sep == '#':
                    if sink != None:
                        sinks.append(sink)
                        sink = None
                    elif inp != None:
                        stream = inp
                        inp = None
                    if key == 'Sink':
                        sink = {'Nr': value}
                    elif key == 'Sink Input':
                        inp = {'Nr': value}
                else:
                    if sink != None:
                        if key in ['Name', 'Description', 'Channel Map', 'Active Port']:
                            sink[key] = value
                        elif key == 'Ports':
                            ports = []
                        if key == 'Active Port':
                            sink['Ports'] = ports
                            ports = None
                        if ports != None:
                            log.debug("getPulseInfo: match port {}".format(value)) 
                            match = portPattern.match(value)
                            if match != None:
                                log.debug("getPulseInfo: matched port {}".format(str(match.groups()))) 
                                ports.append({
                                    'Name': key, 
                                    'Description': match.group(1), 
                                    'Available': (match.group(2).find('not available') == -1)})
                    elif inp != None:
                        if key == 'Sink':
                            inp[key] = int(value)
                        elif key == 'Channel Map':
                            inp[key] = value
                        elif key == 'application.process.id':
                            if os.getpid() != int(value.strip('"')):
                                inp = None
        log.info("getPulseInfo sinks: {}".format(json.dumps(sinks, ensure_ascii=False, sort_keys=True, indent=2, default=str)))
        log.info("getPulseInfo stream: {}".format(json.dumps(stream, ensure_ascii=False, sort_keys=True, indent=2, default=str)))
        self.sinks = sinks
        self.stream = stream
    
    def getPulseDevices(self):
        self.getPulseInfo()
        devices = []
        for sink in self.sinks:
            for port in sink['Ports']:
                devices.append({
                    'device': sink['Nr'] + ':' + port['Name'], 
                    'description': port['Description']})
        return devices
    
    def getAudioOut(self):
        """Enumerate the defined audio output devices.

        @return: list of dicts {name:, description:, devices:} 
                 with devices as list of dicts {device:, description:}
        """
        result = []

        mods = self.instance.audio_output_list_get()
        if mods:
            mod = mods
            while mod:
                mod = mod.contents
                # log.info("init: aolg: contents {}".format(dir(mod)))
                
                devices = []
                devs = self.instance.audio_output_device_list_get(mod.name)
                if devs:
                    dev = devs
                    while dev:
                        dev = dev.contents
                        # log.info("init: aodlg: contents {}".format(dir(dev)))
                        devices.append({
                            'device': dev.device, 
                            'description': vlc.bytes_to_str(dev.description)})
                        dev = dev.next
                
                    vlc.libvlc_audio_output_device_list_release(devs)
                
                if mod.name == b'pulse' and len(devices) == 0:
                    devices = self.getPulseDevices()
                
                result.append({
                    'name': mod.name, 
                    'description': vlc.bytes_to_str(mod.description), 
                    'devices': devices})
                mod = mod.next
                
            vlc.libvlc_audio_output_list_release(mods)
            
        return result

    def setNextAudioOut(self):
        log.debug("setNextAudioOut")
        devices = []
        modules = self.getAudioOut()
        for module in modules:
            devices.append((module["name"], None))
            for device in module["devices"]:
                devices.append((module["name"], device["device"]))

        found = False
        for device in devices:
            if found:
                self.setAudioOut(device[0], device[1])
                return
            if device[0] == self.module and device[1] == self.device:
                found = True

        self.setAudioOut(devices[0][0], devices[0][1])       

    def setAudioOut(self, moduleName, deviceId):
        """Activate device of module for audio output."""
        
        log.debug("setAudioOut module {} device {}".format(moduleName, deviceId))
        
        mediaPos = vlc.libvlc_media_player_get_position(self.player)
        
        if moduleName != self.module:
            log.info("setAudioOut: module {}".format(moduleName))
            status = self.player.audio_output_set(moduleName)
            if status == 0:
                self.module = moduleName
            else:
                log.error("setAudioOut failed module {}".format(moduleName))
        
        if deviceId != self.device:
            log.info("setAudioOut: module {} device {}".format(moduleName, deviceId))
            match = None
            if moduleName == b'pulse' and deviceId != None and self.stream != None:
                match = re.match(r'(\d+):(.*)', deviceId)
            if match != None:
                sink, port = match.groups()
                env = os.environ
                env['LANG'] = 'C'
                subprocess.call(["pactl", "move-sink-input", self.stream['Nr'], sink], env=env)
                subprocess.call(["pactl", "set-sink-port", sink, port], env=env)
            else:
                if deviceId != None:
                    moduleName = None
                self.player.audio_output_device_set(moduleName, deviceId)
            self.device = deviceId

        if mediaPos != -1:
            self.play(self.url, self.song_id)
            vlc.libvlc_media_player_set_position(self.player, mediaPos)
    
    @vlc.callbackmethod
    def onEnd(self, event):
        self.tk.after_idle(self.restart)

    def play(self, url, song_id):
        log.debug('play {}: {}'.format(song_id, url))
        current_media = self.instance.media_new(url)
        if current_media != None:
            log.info('play: {}'.format(current_media.get_mrl()))   
            self.player.stop()
            self.player.set_media(current_media)
            rc = self.player.play()
            if rc != 0:
                log.warn("play failed: {} = {}".format(rc, vlc.libvlc_errmsg()))
            else:
                self.url = url
                self.song_id = song_id
        else:
            log.warn("play failed: No valid media {}".format(url))
            
    def pause(self):
        if self.player.get_state() == vlc.State.Paused:
            log.debug('continue')
        else:
            log.debug('pause')
        self.player.pause()
    
    def restart(self):
        log.debug('restart')
        if self.song_id != None and self.db != None:
            log.debug('update played')
            self.db.songPlayed(self.song_id)
        
        self.play(self.url, self.song_id)
