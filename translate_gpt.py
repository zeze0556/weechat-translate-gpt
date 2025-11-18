# -*- coding: utf-8 -*-
import weechat
import requests
import json
from collections import deque
import os


# ------------------- 配置 -------------------
#OPENAI_API_KEY = "sk-你的API密钥"     # ← 替换这里
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
#OPENAI_API_URL = "http://192.168.2.236:11434/v1/chat/completions" //for local test
MODEL = os.getenv("OPENAI_MODEL","gpt-4o")  # 可改为 gpt-4o, gpt-4o-mini 等
TARGET_LANG = os.getenv("TARGET_LANG","Chinese")
MAX_CONTEXT = int(os.getenv("MAX_CONTEXT", 15)) * 2        # 最近保留多少条上下文消息
SCRIPT_NAME    = "translate_gpt"
SCRIPT_AUTHOR  = "--==RIX==--"
SCRIPT_VERSION = "1.1"
SCRIPT_LICENSE = "MIT"
SCRIPT_DESC    = f"Translate IRC messages into {TARGET_LANG} using OpenAI GPT API with context."

def script_unload_cb():
    """卸载脚本时自动清理 hook"""
    global translate_hook
    if translate_hook:
        weechat.unhook(translate_hook)
        translate_hook = None
    return weechat.WEECHAT_RC_OK


weechat.register(SCRIPT_NAME, SCRIPT_AUTHOR, SCRIPT_VERSION, SCRIPT_LICENSE, SCRIPT_DESC, "script_unload_cb", "")

# 每个频道独立上下文缓存
context_map = {}

def gpt_translate(buffer_name, text):
    """调用 OpenAI API 进行上下文翻译"""
    history = list(context_map.get(buffer_name, []))
    # Rules:
    messages = [{"role": "system", "content": f"""
You are now a simultaneous interpreter in chatroom. Your only task is to instantly and accurately translate any English text I provide into natural, fluent Chinese.
Absolute Rules (must not be violated):
1. Output only the {TARGET_LANG} translation — no explanations, notes, or additional text.
2. Preserve the tone, emotion, and context of the original message.
3. In chat messages formatted as "username: content", Do not treat usernames as separate speakers or characters — Only translate the content after the first colon (:). Keep the username EXACTLY as-is. Do not translate, modify, infer, or replace the username.
4. If I type something that is not in English (e.g., Chinese or a command), do not translate it; just follow the instruction.
5. Do not repeat the English source text.
6. Do not treat usernames as separate characters or roles.
7. No speaker inference.
    - Do NOT assume who “I”, “you”, “he”, “she”, “they” refers to.
    - Translate pronouns literally without assigning them to any username.


From now on, translate every English sentence I send directly into Chinese.
    """
                 }]
    for h in history[-MAX_CONTEXT:]:
        if len(messages) == 1 and h["role"] == "assistant":
            continue
        messages.append(h)

    try:
        response = requests.post(
            OPENAI_API_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": messages,
                #"stream": False,
                "temperature": 0.2,
            },
            timeout=60,
        )
        # 强制使用 UTF-8
        response.encoding = 'utf-8'
        data = response.json()
        ret_message = data["choices"][0]["message"]
        ret_text = ret_message["content"].strip()
        ret_role = ret_message["role"]
        translated = ret_message["content"].strip()
        return translated
    except Exception as e:
        return f"[翻译错误: {e}]"

def incoming_msg_cb(data, buffer, time, tags, displayed, highlight, prefix, message):
    try:
        tags_list = tags.split(",")
        weechat.prnt("", f"incoming_msg_cb tags ={tags} prefix={prefix} message={message}")
        # 只翻译普通聊天消息
        if "irc_privmsg" not in tags_list:
            return weechat.WEECHAT_RC_OK
        self_name = weechat.buffer_get_string(buffer, "localvar_nick")
        # 跳过自己
        if  self_name == prefix:
            return weechat.WEECHAT_RC_OK

        buffer_name = weechat.buffer_get_string(buffer, "name")
        if buffer_name not in context_map:
            context_map[buffer_name] = deque(maxlen=MAX_CONTEXT)

        # 保存用户消息
        user_msg = {"role": "user", "content": f"{prefix}: {message}"}
        context_map[buffer_name].append(user_msg)
        translation = gpt_translate(buffer_name, message)
        # 输出翻译结果
        if translation:
            weechat.prnt(buffer, f"{weechat.color('yellow')}[翻译] {translation}")
            # 保存翻译结果到上下文
            context_map[buffer_name].append({"role": "assistant", "content": translation})
    except Exception as e:
        log_debug("[ERROR] " + str(e))
    return weechat.WEECHAT_RC_OK

# 取消之前可能注册过的 hook（避免重复）
if "translate_hook" in globals() and translate_hook:
    weechat.unhook(translate_hook)

translate_hook = weechat.hook_print("", "", "", 1, "incoming_msg_cb", "")


# ======================
# 消息输入钩子
# ======================
def outgoing_msg_cb(data, buffer, command):
    """
    拦截用户输入消息，进行翻译或直接发送
    """
    # 获取 buffer 名称
    buffer_name = weechat.buffer_get_string(buffer, "name")

    weechat.prnt("", f"{buffer_name} send={command}")
    # 如果以 ! 空格开头，直接发送，不翻译
    if command.startswith("! "):
        command_to_send = command[2:]  # 去掉 "! "
        weechat.command(buffer, command_to_send)
        return weechat.WEECHAT_RC_OK  # 让消息正常发送

    if buffer_name not in context_map:
        context_map[buffer_name] = deque(maxlen=MAX_CONTEXT)
    # 保存用户消息
    prefix = weechat.buffer_get_string(buffer, "localvar_nick")
    user_msg = {"role": "user", "content": f"{prefix}: {command}"}
    context_map[buffer_name].append(user_msg)
    # 否则翻译
    translation = gpt_translate(buffer_name, command)
    if translation:
        # 替换消息内容，发送给服务器
        weechat.command(buffer, translation)
        # 保存翻译结果到上下文
        context_map[buffer_name].append({"role": "assistant", "content": translation})
        return weechat.WEECHAT_RC_OK_EAT  # 阻止原消息重复发送
    return weechat.WEECHAT_RC_OK

# 注册钩子：拦截用户输入
weechat.hook_command("trans_gpt", "", "", "", "", "outgoing_msg_cb", "")

weechat.prnt("", f"{SCRIPT_NAME} loaded. 使用 OpenAI 模型: {MODEL}")
