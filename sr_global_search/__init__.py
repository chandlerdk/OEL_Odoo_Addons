# -*- coding: utf-8 -*-
from . import models

def post_init_hook(env):
    env['global.search.field'].update_search_fields_cache()
