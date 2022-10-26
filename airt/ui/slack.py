# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/ui.slack.ipynb.

# %% auto 0
__all__ = ['logger', 'NS', 'NP', 'P', 'GATHERING_PROMPT', 'STOP_PARSING', 'INTERMEDIATE_KEYS', 'S3_BUCKET_NAME', 'get_key_val',
           'parse_raw_text', 'parse_rich_text_section', 'parse_rich_text_quote', 'parse_rich_text_list',
           'parse_rich_text', 'get_image_from_file', 'build_blocks', 'parse_app_mention_outer_event',
           'upload_image_to_s3', 'handle_app_mention_event']

# %% ../../nbs/ui.slack.ipynb 3
import re
import os
import PIL
import uuid
from PIL import Image
import requests
import io
import base64
import logging
from typing import List
from pprint import pprint, pformat
import pysnooper


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# %% ../../nbs/ui.slack.ipynb 5
def get_key_val(text: str, sep=":") -> dict:
    params = {}
    k, v = [s.strip() for s in text.split(sep)]

    # try:
    #     params[k] = int(v.replace(",", ''))
    # except ValueError:
    #     params[k] = v.replace(",", '')
        
    params[k] = v.replace(",", '')    
    
    return params

# %% ../../nbs/ui.slack.ipynb 6
def parse_raw_text(text: str) -> dict:

    text = re.sub("<@U.*>", "", text)

    params = {}
    # param_key_with_colons = re.findall("[\s,]\w*:\w*[\s,]", text)
    param_key_with_colons = re.findall(f"\w+:\s*[\w.]+", text)
    n = len(param_key_with_colons)
    
    if n == 0 and text:
        params['prompt'] = text.strip()
    elif n > 0:
        remained_text = str(text)
        if n > 1:
            for i, p in enumerate(param_key_with_colons[:-1]):
                start = text.index(p)
                end = text.index(param_key_with_colons[i+1])
                partial_text = text[start: end]
                params.update(get_key_val(partial_text))
                remained_text = remained_text.replace(partial_text, "")
        
        start = text.index(param_key_with_colons[-1])
        end = start + len(param_key_with_colons[-1])
        partial_text = text[start: end]
        params.update(get_key_val(partial_text))
        remained_text = remained_text.replace(partial_text, "")
        
        params['prompt'] = remained_text.strip()
        
    else:
        pass
        # print(f"param_key_with_colons: {param_key_with_colons}")
        # raise NotImplementedError
    

    return params

# %% ../../nbs/ui.slack.ipynb 9
NS = "negative_sentences"
NP = "negative_prompt"
P = "prompt"

GATHERING_PROMPT = "gathering"
STOP_PARSING = "stop"
INTERMEDIATE_KEYS = [GATHERING_PROMPT, STOP_PARSING, NS]

# %% ../../nbs/ui.slack.ipynb 11
# @pysnooper.snoop()
def parse_rich_text_section(element: dict, bot_user_id: str=None, kv_sep=":", is_bullet=False) -> dict:
    
    if not element['type'] in ['rich_text_section', 'rich_text_quote']:
        raise NotImplementedError(pformat(element))
    
    if not bot_user_id and not is_bullet:
        raise ValueError("either `bot_user_id` need to be set or `is_bullet` be True")
    
    d = {}
    partial_prompts = []
    negative_words = []
    bot_mention_found = False
    
    if is_bullet:
        e = element["elements"][0]
        style = e.get("style", {})
        text = e["text"]
        if style and style.get("strike", False):
            logging.info(f"Found negative prompt.")
            negative_words.append(text.strip())                
                
        elif e['type'] == "text" and e.get('text', None):
            logging.info(f"try parsing key value using {kv_sep} separator.")
            
            param_key_with_colons = re.findall(f"\w+{kv_sep}\s*[\w.]+", text)
            # logger.info(f"param_key_with_colons: {param_key_with_colons}")
            for s in param_key_with_colons:
                parts = s.split(kv_sep)
                k, v = parts[0], parts[1]
                d[k.strip()] = v.strip()
        else:
            raise NotImplementedError(e)
    
    else:
        for i, e in enumerate(element['elements']):
            etype = e['type']
            text = e.get("text", None)
            style = e.get("style", {})
            
            if style and style.get("strike", False):
                logging.info(f"Found negative prompt.")
                negative_words.append(text.strip())
            elif etype == "text":
                text = " ".join(re.sub("\s", " ", e['text']).split())
                partial_prompts += [text]
            elif etype == 'user':
                if e['user_id'] == bot_user_id:
                    # clean all previously collected result 
                    partial_prompts = []
                    negative_words = []
                    d = {GATHERING_PROMPT: True}
                    bot_mention_found = True
                else:
                    logging.info(f"Assume only the text after user_id:{bot_user_id} and before another user contains prompt")
                    d.pop(GATHERING_PROMPT, False)
                    d[STOP_PARSING] = True
                    
                    if bot_mention_found:
                        break
                    
            

    if partial_prompts:
        raw_prompt = " ".join([p for p in partial_prompts if p.strip()])
        parsed_from_raw_prompt = parse_raw_text(raw_prompt)
        d.update(parsed_from_raw_prompt)
    if negative_words:
        d[NS] = negative_words
    
    
    return d

# %% ../../nbs/ui.slack.ipynb 23
def parse_rich_text_quote(element: dict, bot_user_id: str=None, kv_sep=":", is_bullet=False) -> dict:
    return parse_rich_text_section(element, bot_user_id=bot_user_id, is_bullet=is_bullet)

# %% ../../nbs/ui.slack.ipynb 25
# @pysnooper.snoop()
def parse_rich_text_list(element: dict) -> dict:
    if not (element['type'] == 'rich_text_list' and element['style'] == "bullet"):
        raise NotImplementedError(pformat(element))
    
    d = {}
    
    for e in element["elements"]:
        partial_d = parse_rich_text_section(e, is_bullet=True)
        d.update(partial_d)
    
    return d

# %% ../../nbs/ui.slack.ipynb 30
# @pysnooper.snoop()
def parse_rich_text(element: dict, bot_user_id: str=None) -> dict:
    if not element['type'] == 'rich_text':
        raise NotImplementedError(pformat(element))
    
    d = {}
    for e in element['elements']:
        etype = e['type']
        
        if etype == 'rich_text_section':
            dd = parse_rich_text_section(e, bot_user_id=bot_user_id)
        elif etype == "rich_text_quote":
            dd = parse_rich_text_quote(e, bot_user_id=bot_user_id)
        elif etype == 'rich_text_list':
            dd = parse_rich_text_list(e)
        else:
            raise NotImplementedError(pformat(e))
        
        logger.info(f"Partial result from parsing {pformat(e)}:")
        logger.info(pformat(dd))
        
         # aggresive sstop
        if STOP_PARSING in dd:
            break
        
        for k, v in dd.items():
            if not k in d:
                d[k] = v
            elif k in d:
                if isinstance(d[k], list):
                    d[k].extend(v)
                else:
                    d[k] = v
                    
            else:
                raise NotImplementedError(f"partial result of {pformat(e)}: {pformat(dd)}")
            
    logger.info("Result by parsing `rich_text` element:")
    logger.info(pformat(d))
    return d
    
    

# %% ../../nbs/ui.slack.ipynb 39
def get_image_from_file(file: dict, token: str):
    # https://stackoverflow.com/a/39849014
    # https://stackoverflow.com/a/36221533
    ftype = file['filetype']
    if ftype != 'png':
        raise NotImplementedError(ftype)
    
    url = file['url_private']
    resp = requests.get(url, headers={'Authorization': 'Bearer %s' % token})
    if resp.status_code == 200:
        content = resp.content
        image = Image.open(io.BytesIO(content))
    else:
        logging.info(resp.status_code)
    
    return image

# %% ../../nbs/ui.slack.ipynb 42
def build_blocks(user_id: str, model_params: str, image_url: str) -> list:
    p = model_params
    logger.info(f"model_params: {pformat(p)}")
    prompt = p['prompt']
    
    b_user_mention = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            f"text": f"<@{user_id}>, here is your artwork :smartart: meow!"
        }
    }
    
    b_divider = {"type": "divider"}
    
    b_prompt = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": prompt
        }
    }
    
    oneline_details = ""
    for k in ['steps', 'cfg', 'seed']:
        oneline_details += f"{k}:{p[k]} "
    
    b_config = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{oneline_details}*"
        }
    }
    
    blocks = [
        b_user_mention,
        b_divider,
        b_prompt,
        b_config
    ]
    
    if p.get("negative_prompt", None):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"~{p['negative_prompt']}~"
            }
        })
        
    image_caption = prompt[:30] + '..' if len(prompt) > 30 else prompt

    b_image = {
        "type": "image",
        "title": {
            "type": "plain_text",
            "text": image_caption,
            "emoji": True
        },
        "image_url": image_url,
        "alt_text": image_caption
    }
    
    blocks.append(b_image)

    return blocks
    

# %% ../../nbs/ui.slack.ipynb 45
# @pysnooper.snoop()
def parse_app_mention_outer_event(outer_event: dict) -> dict:
    auth_info = outer_event['authorizations'][0]
    bot_user_id = auth_info['user_id']
    d = {}
    
    e = outer_event['event']
    if e['type'] != 'app_mention':
        raise NotImplementedError(pformat(e))
    
    for b in e['blocks']:
        if b['type'] != 'rich_text':
            raise NotImplementedError(pformat(b))
        
        dd = parse_rich_text(b, bot_user_id=bot_user_id)
        logger.info(f"Result from parsing block: {pformat(b)}")
        logger.info(pformat(dd))
        
        d.update(dd)
    
    for k in INTERMEDIATE_KEYS:
        if k == NS and k in d:
            logging.info(d[NS])
            d[NP] = " ".join(d[NS])
            
        d.pop(k, None)
        
    orig_d = dict(d)
    for k, v in orig_d.items():
        try:
            d[k] = int(v)

        except ValueError:
            try:
                d[k] = float(v)
            except ValueError:
                continue
                
    # image
    files = e.get('files', [])
    if files:
        logger.info("File detected, try downloading.")
        if len(files) > 1:
            logger.info(f"{len(files)} detected. only use first file as image")
        
        file = files[0]
        token = os.environ['SLACK_BOT_TOKEN']
        image: PIL.PngImagePlugin.PngImageFile = get_image_from_file(file, token=token)
        d['init_image'] = image                 
    
    return d

# %% ../../nbs/ui.slack.ipynb 58
import boto3
S3_BUCKET_NAME = "hackathon2022-smartart"

def upload_image_to_s3(id, user_id, buffer, env="prd"):
    s3 = boto3.resource('s3')
    object_key = f'''{env}/images/{user_id}/{id}.png'''
    s3.Bucket(S3_BUCKET_NAME).put_object(
        Key=object_key,
        Body=buffer,
        ContentType='image/png',
    )
    return f'''s3://{S3_BUCKET_NAME}/{object_key}''' 

# %% ../../nbs/ui.slack.ipynb 59
import tempfile
from smartart.slack.utils import get_user_info, get_conversation_info

def handle_app_mention_event(body, client, logger, model_endpoint):
    log_event = {}
    outer_event = body
    logger.info("outer_event:")
    logger.info(pformat(outer_event))

    params = parse_app_mention_outer_event(outer_event)
    logger.info(f"raw params: {pformat(params)}")
    
    event = outer_event['event']
    text = event['text']
    channel = event['channel']
    user_id = event['user']
    
    # return
    
    
#     user = get_user_info(user_id, client, logger)
#     log_event['user'] = user
#     if user['is_bot']:
#         logger.info("request from bot user, ignored")
#         return
    
#     conversation = get_conversation_info(channel, client, logger)
#     log_event['conversation'] = conversation
    
    
    prompt = params['prompt']
    params['steps'] = params.get("steps", 50)
    params['cfg'] = params.get('cfg', 7.5)
    params['guidance_scale'] = params['cfg']
    params['aspect_ratio'] = params.get('aspect', 1)

    logger.info(f"final params: {pformat(params)}")
    
    
    short_prompt = (prompt[:50] + '..') if len(prompt) > 50 else prompt

    resp = requests.post(
        model_endpoint,
        headers={"Content-Type": "application/json"},
        json=params
    )
    j = resp.json()
    logger.info(j.keys())
    
    image_data = j['images'][0]
    params['seed'] = j['seed']
    
    image = Image.open(io.BytesIO(
        base64.decodebytes(bytes(image_data, "utf-8"))))
    
    tempdir = tempfile.gettempdir()
    file_path = os.path.join(tempdir, f'''{short_prompt}.png''')
    image.save(file_path)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    buffer.seek(0) # rewind pointer back to start
    
    
    # id = uuid.uuid4().hex
    # try:
    #     # TODO: use `env` arg to control the place to save images
    #     image_s3_url = upload_image_to_s3(id, user_id, buffer)
    #     logger.info(image_s3_url)
    # except Exception as e:
    #     image_s3_url = ''
    #     logger.error("Error in upload image to s3", e)
    
#     blocks = build_blocks(user_id, params, image_s3_url)
        
#     client.chat_postMessage(
#         channel=channel, 
#         blocks=blocks
#     )


    oneline_details = ""
    for k in ['steps', 'cfg', 'seed']:
        oneline_details += f"{k}:{params[k]} "


    initial_comment = f"""<@{user_id}>, your artwork is ready.\n{prompt}\n*{oneline_details}*""".strip()
    
    np = params.get("negative_prompt", None)
    if np:
        initial_comment += "\n~" + np + "~"


    file_response = client.files_upload(
        file=file_path,
        channels=[channel],
        initial_comment=initial_comment,
    )
    

    
