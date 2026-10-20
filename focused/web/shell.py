HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Focused</title>
"""

HEADER = """
  <span class="dot" id="dot"></span>
  <span class="title" id="state">[[shell.text.1]]</span>
  <span class="count" id="timer"></span>
  <span class="count" id="killedSummary"></span>
  <span class="spacer"></span>
  <button class="action" id="startPomodoro" onclick="startPomodoroFromSettings()">[[shell.button.1]]</button>
  <button class="action" id="startStopwatch" onclick="startStopwatch()">[[shell.button.2]]</button>
  <button class="action" id="pauseButton" onclick="pauseFromSettings()">[[shell.button.3]]</button>
  <button class="action" id="stopButton" onclick="stopSession()">[[shell.button.4]]</button>
"""

NAV = """
    <button data-tab="overview" class="active">[[shell.button.5]]</button>
    <button data-tab="apps">[[shell.button.6]]</button>
    <button data-tab="urls">[[shell.button.7]]</button>
    <button data-tab="plugins">[[shell.button.8]]</button>
    <button data-tab="settings">[[shell.button.9]]</button>
    <button data-tab="log">[[shell.button.10]]</button>
  
"""
