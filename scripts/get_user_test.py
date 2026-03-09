from bilibili_api import user, sync
import sys

async def main():
    return await user.User(uid=sys.argv[1]).get_user_info()

print(sync(main()))
