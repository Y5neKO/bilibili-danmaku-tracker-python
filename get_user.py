from bilibili_api import user, sync

async def main():
    return await user.User(uid=2).get_user_info()

print(sync(main()))
