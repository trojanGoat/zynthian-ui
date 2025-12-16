#!/usr/bin/python3
# -*- coding: utf-8 -*-
#******************************************************************************
# ZYNTHIAN PROJECT: Zynthian Control Device Driver
#
# Zynthian Control Device Driver for "Novation Launchkey MK4"
#
# Copyright (C) 2015-2023 Fernando Moyano <jofemodo@zynthian.org>
#                         Brian Walton <brian@riban.co.uk>
#                         Jorge Razon <jrazon@gmail.com>
#
#******************************************************************************

import logging
from time import sleep, time

# Zynthian specific modules
from zyngine.ctrldev.zynthian_ctrldev_base import zynthian_ctrldev_zynpad, zynthian_ctrldev_zynmixer
from zyncoder.zyncore import lib_zyncore
from zynlibs.zynseq import zynseq

# ------------------------------------------------------------------------------------------------------------------
# Novation Launchkey MK4 37
# ------------------------------------------------------------------------------------------------------------------

class zynthian_ctrldev_launchkey_mk4_37(zynthian_ctrldev_zynpad, zynthian_ctrldev_zynmixer):

    dev_ids = ["Launchkey MK4 37 DAW In", "Launchkey MK4 37 IN 2"]
    driver_name = "Launchkey MK4 37"
    driver_description = "Interface Novation Launchkey Mk4 with zynpad"

    # --- Configuration Constants ---
    
    # Track Color Map: Maps Zynthian Track ID (Group) to Novation Color ID
    # These colors ensure Pad 1 looks different from Pad 2, matching your track setup.
    PAD_COLOURS = [71, 104, 76, 51, 104, 41, 64, 12, 11, 71, 4, 67, 42, 9, 105, 15]
    
    # Status Colors
    STARTING_COLOUR = 123 # Greenish
    STOPPING_COLOUR = 120 # Reddish
    
    # Launchkey MIDI Note mapping constants
    PAD_ROW_1_START = 96  # Notes 96-103
    PAD_ROW_2_START = 112 # Notes 112-119
    
    # SysEx Header for Novation
    SYS_EX_HEADER = (0xF0, 0x00, 0x20, 0x29, 0x02, 0x14)

    # Static Command Map
    BUTTON_COMMANDS = {
        0x66: "ARROW_RIGHT",
        0x67: "ARROW_LEFT",
        106: "ARROW_UP",
        107: "ARROW_DOWN",
        118: "BACK"
    }

    # Function to initialise class
    def __init__(self, state_manager, idev_in, idev_out=None):
        self.shift = False
        self.mode_cc51 = False
        self.mode_cc52 = False
        self.press_times = {}
        super().__init__(state_manager, idev_in, idev_out)

    def send_sysex(self, data):
        if self.idev_out is not None:
            msg = self.SYS_EX_HEADER + bytes.fromhex(data) + (0xF7,)
            lib_zyncore.dev_send_midi_event(self.idev_out, msg, len(msg))
            sleep(0.05)

    def init(self):
        # Enable DAW mode on launchkey
        if self.idev_out:
            lib_zyncore.dev_send_note_on(self.idev_out, 15, 12, 127)
        self.cols = 8
        self.rows = 2
        super().init()

    def end(self):
        super().end()
        # Disable DAW mode on launchkey
        if self.idev_out:
            lib_zyncore.dev_send_note_on(self.idev_out, 15, 12, 0)
    
    def update_seq_state(self, bank, seq, state, mode, group):
        if self.idev_out is None or bank != self.zynseq.bank:
            return

        # NEW LOGIC: Linear Mapping (Steps 0-15)
        if seq > 15:
            return

        # Map Steps to Launchkey Notes
        if seq < 8:
            note = self.PAD_ROW_1_START + seq
        else:
            note = self.PAD_ROW_2_START + (seq - 8)

        # --- Color Logic ---
        
        # 1. Safety Check: If track index (group) is out of bounds, Default to Color 0 (Off)
        if mode == 0 or group >= len(self.PAD_COLOURS):
            chan = 0
            vel = 0
            
        # 2. Get the specific Track Color from the map
        else:
            track_color = self.PAD_COLOURS[group]

            if state == zynseq.SEQ_STOPPED:
                # Static Track Color
                chan = 0 
                vel = track_color
                
            elif state == zynseq.SEQ_PLAYING:
                # Flashing/Pulsing Track Color (Channel 2 on Launchkey)
                chan = 2
                vel = track_color
                
            elif state in [zynseq.SEQ_STOPPING, zynseq.SEQ_STOPPINGSYNC]:
                chan = 1
                vel = self.STOPPING_COLOUR
                
            elif state == zynseq.SEQ_STARTING:
                chan = 1
                vel = self.STARTING_COLOUR
            else:
                chan = 0
                vel = 0

        lib_zyncore.dev_send_note_on(self.idev_out, chan, note, vel)

    def midi_event(self, ev):
        evtype = (ev[0] >> 4) & 0x0F
        ev_chan = ev[0] & 0x0F
        
        # Handle pad events for the sequencer
        if evtype == 0x9:
            note = ev[1] & 0x7F
            velocity = ev[2] & 0x7F
            
            # Only trigger on Note On (Velocity > 0)
            if velocity > 0:
                pad_index = -1
                
                # Convert Launchkey Note to Linear Step Index (0-15)
                if self.PAD_ROW_1_START <= note <= (self.PAD_ROW_1_START + 7):
                    pad_index = note - self.PAD_ROW_1_START
                elif self.PAD_ROW_2_START <= note <= (self.PAD_ROW_2_START + 7):
                    pad_index = (note - self.PAD_ROW_2_START) + 8
                
                # If valid pad, toggle the corresponding step
                if pad_index > -1:
                    if pad_index < self.zynseq.seq_in_bank:
                        self.zynseq.libseq.togglePlayState(self.zynseq.bank, pad_index)
            return True

        elif evtype == 0xB:
            ccnum = ev[1] & 0x7F
            ccval = ev[2] & 0x7F
            
            # The Launchkey's physical shift button uses CC 0x3F.
            if ccnum == 0x3F:
                self.shift = ccval != 0
                return True

            # Logic for CC 51 and CC 52 (Mixer bank toggles)
            elif ccnum == 51 and ev_chan == 0:
                self.mode_cc51 = (ccval != 0)
                return True
            elif ccnum == 52 and ev_chan == 0:
                self.mode_cc52 = (ccval != 0)
                return True
                
            # Consolidated Knob Logic (21-24)
            elif 20 < ccnum < 25:
                mixer_channel = ccnum - 20
                if self.mode_cc51:
                    mixer_channel += 4
                elif self.mode_cc52:
                    mixer_channel += 8
                    
                chain = self.chain_manager.get_chain_by_position(mixer_channel - 1, midi=False)
                if chain and chain.mixer_chan is not None and chain.mixer_chan < 17:
                    self.zynmixer.set_level(chain.mixer_chan, ccval / 127.0)
                return True

            # Knobs 5-8 for ZYNPOT_ABS (25-28)
            elif 24 < ccnum < 29:
                self.state_manager.send_cuia("ZYNPOT_ABS", [ccnum - 25, ccval / 127])
                return True

            # Combined ZynSwitch and Metronome logic
            elif ccnum in [74, 75, 76, 77]:
                if self.shift and ccnum == 76:
                    if ccval > 0:
                        self.state_manager.send_cuia("TEMPO")
                    return True
                
                zynswitch_index = {74: 0, 75: 1, 76: 3, 77: 2}.get(ccnum)
                if ccval > 0:
                    self.press_times[ccnum] = time()
                else:
                    if ccnum in self.press_times:
                        duration = time() - self.press_times[ccnum]
                        
                        if duration < 0.5:
                            switch_type = 'S' # Short
                        elif duration < 1.5:
                            switch_type = 'B' # Bold
                        else:
                            switch_type = 'L' # Long
                        
                        self.state_manager.send_cuia("ZYNSWITCH", [zynswitch_index, switch_type])
                        del self.press_times[ccnum]
                return True

            # Play / Record Buttons
            elif ccnum == 0x73 and ccval > 0:
                action = "TOGGLE_MIDI_PLAY" if self.shift else "TOGGLE_PLAY"
                self.state_manager.send_cuia(action)
                return True
            elif ccnum == 0x75 and ccval > 0:
                action = "TOGGLE_MIDI_RECORD" if self.shift else "TOGGLE_RECORD"
                self.state_manager.send_cuia(action)
                return True
            
            # Lookup Button Commands from Class Constant
            elif ccnum in self.BUTTON_COMMANDS and ccval > 0:
                self.state_manager.send_cuia(self.BUTTON_COMMANDS[ccnum])
                return True
            
            elif ccnum == 0 or ccval == 0:
                return True

        elif evtype == 0xC:
            val1 = ev[1] & 0x7F
            self.zynseq.select_bank(val1 + 1)

        return True

#-------------------------------------------------------------------------------------------------------
