#!/bin/sh
# 让 nginx 以前台模式运行，交给 supervisord 托管
exec nginx -g 'daemon off;'
