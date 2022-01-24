from bson import ObjectId

import vertex
from apis import perm, try_until_success
from sanic import Sanic, HTTPResponse, Request, json
from config import database, client
from sanic.log import logger

app = Sanic.get_app("SwiftNext")


# 一个函数，用于验证一个字符串的长度是否达标
# 达标返回True，否则返回False
def check_length(string, min_length, max_length):
    if type(string) != str:
        return False
    if len(string) < min_length or len(string) > max_length:
        return False
    else:
        return True


# 一个函数，用于验证一段字符串是否是邮箱的格式
# 使用正则表达式验证
# 是返回True，否则返回False
def check_email(string):
    import re
    if type(string) != str:
        return False
    pattern = re.compile(r'^[A-Za-z0-9\u4e00-\u9fa5]+@[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)+$')
    if pattern.match(string):
        return True
    else:
        return False


# 一个函数 用于验证一个整数是否在指定范围内
# 达标返回True，否则返回False
def check_range(number, min_number, max_number):
    if type(number) != int:
        return False
    if number < min_number or number > max_number:
        return False
    else:
        return True


@app.post("/users")
@perm([0, 3])
async def create_user(request: Request) -> HTTPResponse:
    db = database()
    if request.ctx.session.permission == 0:
        # 针对访客: 尝试验证邮箱验证码 并获取邀请内容
        async def _():
            _temp_user = await db.inactive_users.find_one({
                "_id": ObjectId(request.json.get("code"))
            })
            _invitation = await db.invitations.find_one({
                "_id": ObjectId(_temp_user.get("invitation"))
            })
            return _temp_user, _invitation

        temp_user, invitation = await try_until_success(_)
        if temp_user is None:
            # 邮箱验证码不正确 或 验证码已过期
            return json({
                "code": 1001,
                "message": {
                    "cn": "该验证链接不存在或已过期",
                    "en": "The link is not valid or has expired"
                },
                "description": {
                    "code": request.json.get("code"),
                }
            }, 406)
        elif invitation is None:
            # 邀请已经过期
            return json({
                "code": 1002,
                "message": {
                    "cn": "邀请已经过期",
                    "en": "The invitation has expired"
                },
                "description": {
                    "code": request.json.get("code"),
                    "invitation": request.json.get("invitation")
                }
            }, 406)
        elif invitation.get("type") != "register":
            # 邀请类型不正确
            return json({
                "code": 1003,
                "message": {
                    "cn": "邀请类型不正确",
                    "en": "The invitation type is incorrect"
                },
                "description": {
                    "code": request.json.get("code"),
                    "invitation": request.json.get("invitation")
                }
            }, 406)
        else:
            # 创建新的事务
            c = client()
            db = c.swiftnext
            try:
                with c.start_session(causal_consistency=True) as session:
                    # with 保证session的正确关闭
                    with session.start_transaction():
                        # 1. 创建用户
                        result = await db.users.insert_one({
                            "email": temp_user["email"],
                            "password": temp_user["password"],
                            "permission": invitation["permission"],
                            "group_id": invitation["group_id"],
                        })
                        uid = result.inserted_id
                        logger.info("新用户的ID: {}".format(uid))
                        # 2. 删除邀请
                        await db.invitations.delete_one({
                            "_id": ObjectId(invitation["_id"])
                        })
                        # 3. 删除临时用户
                        await db.inactive_users.delete_one({
                            "_id": ObjectId(temp_user["_id"])
                        })
                return json({
                    "uid": str(uid),
                    "group_id": invitation["group_id"],
                    "inviter": invitation["inviter"],
                })
            except BaseException as e:
                return json({
                    "code": 3,
                    "message": {
                        "cn": "创建用户时发生了意外，请稍后再试",
                        "en": "An unexpected error occurred while creating a user, please try again later"
                    },
                    "description": {
                        "error": str(e)
                    }
                }, 500)
    else:
        # 针对管理员: 直接创建用户
        c = client()
        db = c.swiftnext
        # 校验字段
        if not check_length(request.json.get("name"), 2, 32):
            return json({
                "code": 4,
                "message": {
                    "cn": "用户姓名长度不正确",
                    "en": "The user name length is incorrect"
                },
                "description": {
                    "name": request.json.get("name"),
                    "max": 32,
                    "min": 2
                }
            }, 406)
        if not check_length(request.json.get("password"), 32, 32):
            return json({
                "code": 4,
                "message": {
                    "cn": "加密后密码长度不正确",
                    "en": "The encrypted password length is incorrect"
                },
                "description": {
                    "password": request.json.get("password"),
                    "max": 32,
                    "min": 32
                }
            }, 406)
        # 验证邮箱格式是否正确
        # 使用check_email函数验证
        if not check_email(request.json.get("email")):
            return json({
                "code": 4,
                "message": {
                    "cn": "邮箱格式不正确",
                    "en": "The email format is incorrect"
                },
                "description": {
                    "email": request.json.get("email")
                }
            }, 406)
        # 检验权限字段是否合法
        # 合法的权限应该在 0-3 之间
        if not check_range(request.json.get("permission"), 0, 3):
            return json({
                "code": 4,
                "message": {
                    "cn": "权限不正确",
                    "en": "The permission is incorrect"
                },
                "description": {
                    "permission": request.json.get("permission"),
                    "max": 3,
                    "min": 0
                }
            }, 406)

        # 检验小组id是否合法
        async def _():
            return await db.groups.find_one({
                "_id": ObjectId(request.json.get("group_id"))
            }) is not None

        if not await try_until_success(_):
            return json({
                "code": 4,
                "message": {
                    "cn": "小组不存在",
                    "en": "The group does not exist"
                },
                "description": {
                    "group_id": request.json.get("group_id")
                }
            }, 406)

        try:
            with c.start_session(causal_consistency=True) as session:
                # with 保证session的正确关闭
                with session.start_transaction():
                    # 创建用户
                    result = await db.users.insert_one({
                        "email": request.json.get("email"),
                        "password": request.json.get("password"),
                        "permission": request.json.get("permission"),
                        "group_id": request.json.get("group_id"),
                    })
                    uid = result.inserted_id
            # 成功返回新用户的uid
            return json({
                "uid": str(uid),
            })
        except BaseException as e:
            return json({
                "code": 3,
                "message": {
                    "cn": "创建用户时发生了意外，请稍后再试",
                    "en": "An unexpected error occurred while creating a user, please try again later"
                },
                "description": {
                    "error": str(e)
                }
            }, 500)


@app.post("/users/check_email")
async def check_by_email(request) -> HTTPResponse:
    """
    检查某个邮箱是否存在用户
    :param request:
    :return:
    """
    db = database()
    # 查询邮箱是否存在 不查询password
    result = await db.users.find_one({
        "email": request.json.get("email")
    }, {"password": 0})
    if result is not None:
        result['uid'] = str(result['_id'])
        result.pop("_id")
        return json(result)
    else:
        return json({
            "code": 1001,
            "message": {
                "cn": "这个邮箱没有注册过",
                "en": "This email has not been registered"
            },
            "description": {
                "email": request.json.get("email")
            }
        }, 404)


@app.post("/users/login")
async def login(request: Request) -> HTTPResponse:
    """
    登录
    :param request:
    :return:
    """
    email = request.json.get("email")
    password = request.json.get("password")
    if not check_email(email):
        return json({
            "code": 4,
            "message": {
                "cn": "邮箱格式不正确",
                "en": "The email format is incorrect"
            },
            "description": {
                "email": email
            }
        }, 406)
    if not check_length(password, 32, 32):
        return json({
            "code": 4,
            "message": {
                "cn": "密码格式不正确",
                "en": "The password format is incorrect"
            },
            "description": {
                "password": password
            }
        }, 406)

    db = database()
    # 在数据库中查询相关用户
    result = await db.users.find_one({
        "email": email,
        "password": password
    }, {"password": 0})
    if result is not None:
        # 检查相关用户是否被封禁
        # 去掉ObjectId
        result['uid'] = str(result['_id'])
        result.pop("_id")
        banned = await db.restrictions.find_one({
            "uid": result['uid']
        })
        if banned is not None:
            # 该用户已经被封禁
            return json({
                "code": 1002,
                "message": {
                    "cn": "该用户已被封禁",
                    "en": "The user has been banned"
                },
                "description": {
                    "uid": result['uid'],
                    "reason": banned['reason'],
                    "banned_time": banned['banned_time'],
                },
            }, 403)
        # 登录成功
        # 更新用户的session
        request.ctx.session_need_update = True
        request.ctx.session['login'] = True
        request.ctx.session['user'] = result
        request.ctx.session['permission'] = result['permission']
        return json(result)
    else:
        return json({
            "code": 1001,
            "message": {
                "cn": "邮箱或密码错误",
                "en": "Email or password is incorrect"
            },
            "description": {
                "email": email,
                "password": password
            }
        }, 401)