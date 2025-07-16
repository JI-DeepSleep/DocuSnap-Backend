# DocuSnap - Backend Server

The repo that stores the code for DocuSnap's backend server.

## For Frontend Developers

### APIs

Please refer to [https://github.com/JI-DeepSleep/DocuSnap?tab=readme-ov-file#backend-server-flask](https://github.com/JI-DeepSleep/DocuSnap?tab=readme-ov-file#backend-server-flask) Backend Server (Flask) chapter. You should only be interacting with the backend server. 

The root of our deployment of the backend is `https://docusnap.zjyang.dev/api/v1/`, for example, you can check the server status at `https://docusnap.zjyang.dev/api/v1/check_status`.

## For Backend Developers

The backend actually consists of 3 parts:

- Backend server and cache server: The backend server is the one called by the app and the one coordinates task processing with the OCR server and an LLM provider. The cache server right now is just a sqlite3 db, but both logically and technically you can separate it from the backend server. 
- OCR server: We use CnOCR for this project. 
- LLM provider: We use Zhipu LLM for this project. 

To start developing, you need to have all 3 pieces. 

You need two folders, one for CnOCR and one for the backend. 

First, create a folder for CnOCR and have python venv setup in that folder.

Install CnOCR in that venv according to [https://github.com/breezedeus/CnOCR](https://github.com/breezedeus/CnOCR):

```bash
pip install "cnocr[ort-cpu]"
pip install "cnocr[serve]""
```

After that you should be able to start the server on port 14410:

```bash
cnocr serve -p 14410
```

Visit [http://localhost:14410/](http://localhost:14410/) and you should see the message `{"message":"Welcome to CnOCR Server!"}`

Second, create another folder and clone this project. 

Also create a python venv for the folder.

Use `pip install -r requirements.txt` to install all the dependencies.

Run `pip freeze` to check the version of the ZhipuAI python package. If it says `2.1.5.20250708`, there's a bug in it and you need to fix it:

- Open `env/lib/python3.12/site-packages/zhipuai/api_resource/chat/async_completions.py`

- Add two lines to the code:

- ```python
  ...
              extra_body: Body | None = None,
              timeout: float | httpx.Timeout | None | NotGiven = NOT_GIVEN,
              response_format : object | None = None    # ADD THIS LINE
      ) -> AsyncTaskStatus:
          _cast_type = AsyncTaskStatus
          logger.debug(f"temperature:{temperature}, top_p:{top_p}")
          if temperature is not None and temperature != NOT_GIVEN:
  ...
              "tool_choice": tool_choice,
              "meta": meta,
              "extra": maybe_transform(extra, code_geex_params.CodeGeexExtra),
              "response_format": response_format    # ADD THIS LINE
          }
          return self._post(
              "/async/chat/completions",
  
              body=body,
              options=make_request_options(
                  extra_headers=extra_headers, extra_body=extra_body, timeout=timeout
  ...
  ```

Next, you wanna setup the backend RSA keys and other private settings. 

- If you're a member of Team DeepSleep, head to the private repo and copy everything in it to the backend project root folder and you're ready to go. 
- If you're not a member of Team DeepSleep:
  - Run `genKeyPairs.sh` to generate an RSA key pairs. 
  - Copy `priv_sets.py.sample` to `priv_sets.py`
  - Head to [Zhipu LLM](https://bigmodel.cn/) and get and API key, fill it in `priv_sets.py`.
  - Go to `templates/ocr.html` and search for things related to `docusnap.zjyang.dev` and change that to your backend URL. 

Now, `python3 app.py` and you should be able to visit the website at [http://127.0.0.1:5000/](http://127.0.0.1:5000/) or whatever port you use. 

If you visit that URL, you'll find that we've implemented a mockup webui to easy backend developing. Try some sample images in the sample folder to make sure it is working! Of course, this is just a mockup webui and is not the whole DocuSnap experience (for example, the file_lib is hardcoded in the webui).

### Start Prompt Engineering

Prompts are stored in `prompts.py`

Checkout the variables for inspiration: `DEFAULT_FIELDS` and `DOC_PROMPT`.

Then check `app.py` and look for `process_task` function. The `construct_prompt_*` are functions that construct the prompts, which are then fed to the llm by the `call_llm` function.

## Deploying the Backend

If you're in Team DeepSleep, contact Zijun to get an ed25519 key to the server.

If you're not in Team DeepSleep, we recommend following the best practice of deploying a flask project (with gunicorn + nginx for example). 
