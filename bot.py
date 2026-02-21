import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import math
import re
from datetime import timedelta
import random
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from gtts import gTTS

# ---------------- CONFIG ----------------

with open("config.json", "r") as f:
    config = json.load(f)

TOKEN = os.getenv("DISCORD_BOT_TOKEN") or config.get("token", "")
XP_PER_MESSAGE = config["xp_per_message"]
MIN_MSG_LEN = config["min_message_length"]
BASE_XP = config["base_xp_per_level"]
XP_GROWTH = config["xp_growth_percent"] / 100
ADMIN_ROLE = config["admin_role_name"]

DATA_FILE = "data.json"

# ---------------- DATA ----------------

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

data = load_data()

def ensure_user(uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = {}
    data[uid].setdefault("xp", 0)
    data[uid].setdefault("level", 0)
    data[uid].setdefault("messages", 0)
    data[uid].setdefault("warns", 0)
    data[uid].setdefault("money", 0)
    data[uid].setdefault("cooldowns", {})

# ---------------- HELPERS ----------------
class SoundBoard(discord.ui.View):
    def __init__(self, sound_files, page=0):
        super().__init__(timeout=120)
        self.sound_files = sound_files
        self.page = page
        self.per_page = 23  # 25 max components - 2 nav buttons
        self.total_pages = (len(sound_files) - 1) // self.per_page + 1
        self.build_page()

    def build_page(self):
        self.clear_items()

        start = self.page * self.per_page
        end = start + self.per_page
        current_sounds = self.sound_files[start:end]

        # Sound buttons (5 per row)
        for index, sound in enumerate(current_sounds):
            row = index // 5
            self.add_item(SoundButton(sound, row))

        # Navigation row (row 4)
        if self.total_pages > 1:

            if self.page > 0:
                self.add_item(PrevButton())

            if self.page < self.total_pages - 1:
                self.add_item(NextButton())


class PrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="⬅ Previous",
            style=discord.ButtonStyle.secondary,
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        view: SoundBoard = self.view
        view.page -= 1
        view.build_page()

        await interaction.response.edit_message(
            content=f"🎵 **Soundboard** — Page {view.page + 1} / {view.total_pages}",
            view=view
        )


class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Next ➡",
            style=discord.ButtonStyle.secondary,
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        view: SoundBoard = self.view
        view.page += 1
        view.build_page()

        await interaction.response.edit_message(
            content=f"🎵 **Soundboard** — Page {view.page + 1} / {view.total_pages}",
            view=view
        )


class SoundButton(discord.ui.Button):
    def __init__(self, filename: str, row: int):
        label = filename.replace(".mp3", "")

        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            row=row
        )

        self.filename = filename

    async def callback(self, interaction: discord.Interaction):

        voice_client = interaction.guild.voice_client

        if not voice_client:
            await interaction.response.send_message(
                "❌ Connect me to a voice channel first with /mango-join",
                ephemeral=True
            )
            return

        base_dir = os.path.dirname(os.path.abspath(__file__))
        sounds_dir = os.path.join(base_dir, "sounds")
        file_path = os.path.join(sounds_dir, self.filename)

        if voice_client.is_playing():
            voice_client.stop()

        voice_client.play(discord.FFmpegPCMAudio(file_path))

        await interaction.response.send_message(
            f"🔊 Playing `{self.filename}`",
            ephemeral=True
        )

def check_level_up(uid):
    ensure_user(uid)
    user = data[str(uid)]

    while user["xp"] >= xp_needed(user["level"]):
        user["xp"] -= xp_needed(user["level"])
        user["level"] += 1

def xp_needed(level):
    return math.floor(BASE_XP * ((1 + XP_GROWTH) ** level))

def is_admin(member: discord.Member):
    return member.guild_permissions.administrator or any(
        r.name == ADMIN_ROLE for r in member.roles
    )

def admin_only():
    async def predicate(interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ Admin only command.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

def parse_time(t):
    if t.lower() == "inf":
        return None
    m = re.match(r"(\d+)(s|m|h|d)", t)
    if not m:
        return timedelta(minutes=5)
    num, unit = m.groups()
    return timedelta(**{
        "s": {"seconds": int(num)},
        "m": {"minutes": int(num)},
        "h": {"hours": int(num)},
        "d": {"days": int(num)}
    }[unit])

def check_cooldown(uid, key, cooldown_seconds):
    now = time.time()
    ensure_user(uid)
    last = data[str(uid)]["cooldowns"].get(key, 0)
    if now - last < cooldown_seconds:
        return cooldown_seconds - (now - last)
    data[str(uid)]["cooldowns"][key] = now
    save_data()
    return 0

# ---------------- VOICE / TTS ----------------

executor = ThreadPoolExecutor()

def generate_tts(text: str, filename: str, lang: str = "fi"):
    tts = gTTS(text=text, lang=lang)
    tts.save(filename)

async def generate_tts_async(text: str, filename: str, lang: str = "fi"):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, generate_tts, text, filename, lang)

# ---------------- BOT ----------------

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.tree.sync()
    bot.loop.create_task(console_listener())  # Start console system

# ---------------- VOICE COMMANDS ----------------

# Top 25 most used languages (name, code)
AVAILABLE_LANGUAGES = [
    ("English", "en"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Chinese", "zh"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Russian", "ru"),
    ("Portuguese", "pt"),
    ("Italian", "it"),
    ("Dutch", "nl"),
    ("Arabic", "ar"),
    ("Hindi", "hi"),
    ("Bengali", "bn"),
    ("Turkish", "tr"),
    ("Vietnamese", "vi"),
    ("Polish", "pl"),
    ("Swedish", "sv"),
    ("Norwegian", "no"),
    ("Danish", "da"),
    ("Finnish", "fi"),
    ("Greek", "el"),
    ("Hebrew", "he"),
    ("Thai", "th"),
    ("Indonesian", "id")
]

# Convert to app_commands.Choice objects for Discord dropdown
language_choices = [
    app_commands.Choice(name=name, value=code)
    for name, code in AVAILABLE_LANGUAGES
]

# ---------------- JOIN VC ----------------
@bot.tree.command(name="mango-join", description="Make the bot join your voice channel")
async def mango_join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel to use this command.", ephemeral=True)
        return
    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    if vc and vc.channel == channel:
        await interaction.response.send_message("✅ Already connected to your voice channel.", ephemeral=True)
        return
    elif vc:
        await vc.move_to(channel)
    else:
        await channel.connect()
    await interaction.response.send_message(f"✅ Connected to **{channel.name}**.", ephemeral=True)

# ---------------- SAY COMMAND ----------------
@bot.tree.command(name="say", description="Make the bot speak in VC")
@app_commands.describe(
    text="Text to speak",
    language="Optional language (default: Finnish)"
)
@app_commands.choices(language=language_choices)
async def say(interaction: discord.Interaction, text: str, language: str = "fi"):

    voice_client = interaction.guild.voice_client

    if not voice_client:
        await interaction.response.send_message(
            "❌ Connect me to a voice channel first with /mango-join",
            ephemeral=True
        )
        return

    # Validate language
    lang_code = language.lower() if language.lower() in [c for _, c in AVAILABLE_LANGUAGES] else "fi"
    lang_name = next((n for n, c in AVAILABLE_LANGUAGES if c == lang_code), "Finnish")

    await interaction.response.defer()  # defer immediately

    filename = f"tts_{interaction.id}.mp3"

    # Generate TTS asynchronously
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, lambda: gTTS(text=text, lang=lang_code).save(filename))

    # Play audio in VC
    if voice_client.is_playing():
        voice_client.stop()
    voice_client.play(discord.FFmpegPCMAudio(filename))

    # Wait until finished
    while voice_client.is_playing():
        await asyncio.sleep(1)

    # Clean up
    os.remove(filename)

    await interaction.followup.send(
        f"🗣️ Done speaking in **{lang_name}**.",
        ephemeral=True
    )

# ---------------- USER COMMANDS ----------------

@bot.tree.command(name="xp")
async def xp(interaction: discord.Interaction, user: discord.Member = None):
    t = user or interaction.user
    ensure_user(t.id)
    u = data[str(t.id)]
    await interaction.response.send_message(
        f"⭐ {t.mention}: {u['xp']} XP | Next {xp_needed(u['level'])}"
    )

@bot.tree.command(name="lvl")
async def lvl(interaction: discord.Interaction, user: discord.Member = None):
    t = user or interaction.user
    ensure_user(t.id)
    await interaction.response.send_message(
        f"📊 {t.mention} is **Level {data[str(t.id)]['level']}**"
    )

@bot.tree.command(name="messages")
async def messages(interaction: discord.Interaction, user: discord.Member = None):
    t = user or interaction.user
    ensure_user(t.id)
    await interaction.response.send_message(
        f"💬 {t.mention}: {data[str(t.id)]['messages']} messages"
    )

@bot.tree.command(name="leaderboard")
@app_commands.choices(type=[
    app_commands.Choice(name="XP / Level", value="xp"),
    app_commands.Choice(name="Messages", value="messages"),
    app_commands.Choice(name="Money", value="money"),
])
async def leaderboard(interaction: discord.Interaction, type: app_commands.Choice[str]):
    for uid in data:
        ensure_user(uid)

    if type.value == "money":
        key = lambda x: x[1]["money"]
        title = "💰 Money Leaderboard"
    elif type.value == "messages":
        key = lambda x: x[1]["messages"]
        title = "💬 Message Leaderboard"
    else:
        key = lambda x: x[1]["level"] * 1_000_000 + x[1]["xp"]
        title = "🏆 XP & Level Leaderboard"

    sorted_users = sorted(data.items(), key=key, reverse=True)
    embed = discord.Embed(title=title, color=discord.Color.gold())

    for i, (uid, d) in enumerate(sorted_users[:10], 1):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else uid

        value = (
            f"${d['money']}" if type.value == "money"
            else f"{d['messages']} msgs" if type.value == "messages"
            else f"Lvl {d['level']} ({d['xp']} XP)"
        )

        embed.add_field(name=f"{i}. {name}", value=value, inline=False)

    await interaction.response.send_message(embed=embed)

# ---------------- ECONOMY ----------------

@bot.tree.command(name="balance")
async def balance(interaction: discord.Interaction, user: discord.Member = None):
    t = user or interaction.user
    ensure_user(t.id)
    await interaction.response.send_message(f"💰 {t.mention} has ${data[str(t.id)]['money']}")

@bot.tree.command(name="pay")
async def pay(interaction: discord.Interaction, user: discord.Member, amount: int):
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.")
        return

    ensure_user(interaction.user.id)
    ensure_user(user.id)

    sender = data[str(interaction.user.id)]
    receiver = data[str(user.id)]

    if sender["money"] < amount:
        await interaction.response.send_message("❌ You don't have enough money.")
        return

    sender["money"] -= amount
    receiver["money"] += amount
    save_data()
    await interaction.response.send_message(f"💸 {interaction.user.mention} paid ${amount} to {user.mention}")

# ---------------- CASINO COMMANDS ----------------

@bot.tree.command(name="work")
async def work(interaction: discord.Interaction):
    cd = check_cooldown(interaction.user.id, "work", 3600)  # 1h cooldown
    if cd:
        await interaction.response.send_message(f"⏱️ Wait {int(cd)//60}m {int(cd)%60}s before working again.")
        return
    earned = random.randint(50, 200)
    ensure_user(interaction.user.id)
    data[str(interaction.user.id)]["money"] += earned
    save_data()
    await interaction.response.send_message(f"💼 You worked and earned ${earned}!")

@bot.tree.command(name="crime")
async def crime(interaction: discord.Interaction):
    cd = check_cooldown(interaction.user.id, "crime", 7200)  # 2h cooldown
    if cd:
        await interaction.response.send_message(f"⏱️ Wait {int(cd)//60}m {int(cd)%60}s before committing a crime again.")
        return
    success = random.choice([True, False])
    ensure_user(interaction.user.id)
    if success:
        earned = random.randint(100, 400)
        data[str(interaction.user.id)]["money"] += earned
        save_data()
        await interaction.response.send_message(f"💰 Crime succeeded! You got ${earned}.")
    else:
        lost = random.randint(50, 200)
        data[str(interaction.user.id)]["money"] = max(0, data[str(interaction.user.id)]["money"] - lost)
        save_data()
        await interaction.response.send_message(f"❌ Crime failed! You lost ${lost}.")

@bot.tree.command(name="rob")
async def rob(interaction: discord.Interaction, user: discord.Member):
    if user.bot:
        await interaction.response.send_message("❌ You can't rob bots.")
        return
    cd = check_cooldown(interaction.user.id, "rob", 10800)  # 3h cooldown
    if cd:
        await interaction.response.send_message(f"⏱️ Wait {int(cd)//60}m {int(cd)%60}s before robbing again.")
        return
    ensure_user(interaction.user.id)
    ensure_user(user.id)
    target_money = data[str(user.id)]["money"]
    if target_money <= 0:
        await interaction.response.send_message(f"❌ {user.mention} has no money to rob.")
        return
    stolen = random.randint(1, target_money)
    data[str(interaction.user.id)]["money"] += stolen
    data[str(user.id)]["money"] -= stolen
    save_data()
    await interaction.response.send_message(f"🕵️ You robbed ${stolen} from {user.mention}!")

# ---------------- ADMIN: XP / LEVEL ----------------

@bot.tree.command(name="givexp")
@admin_only()
async def givexp(interaction: discord.Interaction, user: discord.Member, amount: float):
    ensure_user(user.id)
    data[str(user.id)]["xp"] += amount
    save_data()
    await interaction.response.send_message("✅ XP given.")

@bot.tree.command(name="takexp")
@admin_only()
async def takexp(interaction: discord.Interaction, user: discord.Member, amount: float):
    ensure_user(user.id)
    data[str(user.id)]["xp"] = max(0, data[str(user.id)]["xp"] - amount)
    save_data()
    await interaction.response.send_message("✅ XP taken.")

@bot.tree.command(name="setxp")
@admin_only()
async def setxp(interaction: discord.Interaction, user: discord.Member, amount: float):
    ensure_user(user.id)
    data[str(user.id)]["xp"] = max(0, amount)
    save_data()
    await interaction.response.send_message("✅ XP set.")

@bot.tree.command(name="givelvl")
@admin_only()
async def givelvl(interaction: discord.Interaction, user: discord.Member, amount: int):
    ensure_user(user.id)
    data[str(user.id)]["level"] += amount
    save_data()
    await interaction.response.send_message("✅ Level(s) given.")

@bot.tree.command(name="takelvl")
@admin_only()
async def takelvl(interaction: discord.Interaction, user: discord.Member, amount: int):
    ensure_user(user.id)
    data[str(user.id)]["level"] = max(0, data[str(user.id)]["level"] - amount)
    save_data()
    await interaction.response.send_message("✅ Level(s) taken.")

@bot.tree.command(name="setlvl")
@admin_only()
async def setlvl(interaction: discord.Interaction, user: discord.Member, level: int):
    ensure_user(user.id)
    data[str(user.id)]["level"] = max(0, level)
    data[str(user.id)]["xp"] = 0
    save_data()
    await interaction.response.send_message("✅ Level set.")

# ---------------- ADMIN: MONEY ----------------

@bot.tree.command(name="givemoney")
@admin_only()
async def givemoney(interaction: discord.Interaction, user: discord.Member, amount: int):
    ensure_user(user.id)
    data[str(user.id)]["money"] += max(0, amount)
    save_data()
    await interaction.response.send_message("💰 Money given.")

@bot.tree.command(name="takemoney")
@admin_only()
async def takemoney(interaction: discord.Interaction, user: discord.Member, amount: int):
    ensure_user(user.id)
    data[str(user.id)]["money"] = max(0, data[str(user.id)]["money"] - amount)
    save_data()
    await interaction.response.send_message("💸 Money taken.")

@bot.tree.command(name="setmoney")
@admin_only()
async def setmoney(interaction: discord.Interaction, user: discord.Member, amount: int):
    ensure_user(user.id)
    data[str(user.id)]["money"] = max(0, amount)
    save_data()
    await interaction.response.send_message("💰 Money set.")

# ---------------- ADMIN: MODERATION ----------------

@bot.tree.command(name="warn")
@admin_only()
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    ensure_user(user.id)
    data[str(user.id)]["warns"] += 1
    save_data()
    await interaction.response.send_message(
        f"⚠️ {user.mention} warned | Total: {data[str(user.id)]['warns']}"
    )

@bot.tree.command(name="warns")
@admin_only()
async def warns(interaction: discord.Interaction, user: discord.Member):
    ensure_user(user.id)
    await interaction.response.send_message(
        f"⚠️ {user.mention} has {data[str(user.id)]['warns']} warns"
    )

@bot.tree.command(name="to")
@admin_only()
async def timeout(interaction: discord.Interaction, user: discord.Member, duration: str):
    delta = parse_time(duration)
    until = None if delta is None else discord.utils.utcnow() + delta
    await user.timeout(until)
    await interaction.response.send_message("⏱️ Timeout applied.")

@bot.tree.command(name="role")
@admin_only()
async def role_cmd(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    if role in user.roles:
        await user.remove_roles(role)
        msg = "removed"
    else:
        await user.add_roles(role)
        msg = "added"
    await interaction.response.send_message(f"🔧 Role {msg}.")

@bot.tree.command(name="ban")
@admin_only()
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    await user.ban(reason=reason)
    await interaction.response.send_message("🔨 User banned.")
 	
# ---------------- CONSOLE COMMANDS ----------------
async def console_listener():
    await bot.wait_until_ready()
    print("🖥️ Console command system ready.")

    loop = asyncio.get_running_loop()

    while not bot.is_closed():
        command = await loop.run_in_executor(None, input, "")

        if not command:
            continue

        args = command.strip().split()
        cmd = args[0].lower()

        try:
            guild = bot.guilds[0] if bot.guilds else None

            # ---------------- STOP ----------------
            if cmd == "stop":
                print("🛑 Shutting down bot...")
                await bot.close()

            # ---------------- JOIN VC ----------------
            elif cmd == "join":
                if not guild:
                    print("❌ No guild found.")
                    continue

                for member in guild.members:
                    if member.voice and member.voice.channel:
                        await member.voice.channel.connect()
                        print(f"✅ Joined {member.voice.channel.name}")
                        break
                else:
                    print("❌ No user in voice channel.")

            # ---------------- SAY ----------------
            elif cmd == "say":
                if len(args) < 2:
                    print("Usage: say <text>")
                    continue

                if not guild or not guild.voice_client:
                    print("❌ Bot not connected to VC.")
                    continue

                text = " ".join(args[1:])
                vc = guild.voice_client
                filename = "console_tts.mp3"

                await loop.run_in_executor(
                    executor,
                    lambda: gTTS(text=text, lang="fi").save(filename)
                )

                if vc.is_playing():
                    vc.stop()

                vc.play(discord.FFmpegPCMAudio(filename))

                while vc.is_playing():
                    await asyncio.sleep(1)

                os.remove(filename)
                print("🗣️ Spoke in VC.")

            # ---------------- XP ----------------
            elif cmd == "givexp" and len(args) == 3:
                user_id = args[1]
                amount = float(args[2])
                ensure_user(user_id)
                data[str(user_id)]["xp"] += amount
                save_data()
                print(f"⭐ Gave {amount} XP to {user_id}")

            elif cmd == "setxp" and len(args) == 3:
                user_id = args[1]
                amount = float(args[2])
                ensure_user(user_id)
                data[str(user_id)]["xp"] = amount
                save_data()
                print(f"⭐ Set XP to {amount} for {user_id}")

            elif cmd == "setlvl" and len(args) == 3:
                user_id = args[1]
                level = int(args[2])
                ensure_user(user_id)
                data[str(user_id)]["level"] = level
                data[str(user_id)]["xp"] = 0
                save_data()
                print(f"📊 Set level {level} for {user_id}")

            # ---------------- MONEY ----------------
            elif cmd == "givemoney" and len(args) == 3:
                user_id = args[1]
                amount = int(args[2])
                ensure_user(user_id)
                data[str(user_id)]["money"] += amount
                save_data()
                print(f"💰 Gave ${amount} to {user_id}")

            elif cmd == "setmoney" and len(args) == 3:
                user_id = args[1]
                amount = int(args[2])
                ensure_user(user_id)
                data[str(user_id)]["money"] = amount
                save_data()
                print(f"💰 Set money to ${amount} for {user_id}")

            # ---------------- WARN ----------------
            elif cmd == "warn" and len(args) >= 2:
                user_id = args[1]
                ensure_user(user_id)
                data[str(user_id)]["warns"] += 1
                save_data()
                print(f"⚠️ Warned {user_id}")

            elif cmd == "user" and len(args) == 2:
                user_id = args[1]
                ensure_user(user_id)
                print(data[str(user_id)])

            else:
                print("❌ Unknown console command.")

        except Exception as e:
            print(f"⚠️ Console error: {e}")



# ---------------- SOUND COMMAND ----------------

@bot.tree.command(name="sound", description="Play an MP3 file from the sounds folder")
@app_commands.describe(filename="Sound name (example: test or test.mp3)")
async def sound(interaction: discord.Interaction, filename: str):

    voice_client = interaction.guild.voice_client

    if not voice_client:
        await interaction.response.send_message(
            "❌ Connect me to a voice channel first with /mango-join",
            ephemeral=True
        )
        return

    # Sanitize
    filename = filename.lower().replace(".mp3", "").strip()
    filename = f"{filename}.mp3"

    if "/" in filename or "\\" in filename:
        await interaction.response.send_message(
            "❌ Invalid filename.",
            ephemeral=True
        )
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    sounds_dir = os.path.join(base_dir, "sounds")
    file_path = os.path.join(sounds_dir, filename)

    if not os.path.isfile(file_path):
        await interaction.response.send_message(
            f"❌ Sound `{filename}` not found in sounds folder.",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    if voice_client.is_playing():
        voice_client.stop()

    voice_client.play(discord.FFmpegPCMAudio(file_path))

    await interaction.followup.send(
        f"🔊 Playing `{filename}`.",
        ephemeral=True
    )

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if len(message.content) >= MIN_MSG_LEN:
        ensure_user(message.author.id)
        user = data[str(message.author.id)]
        user["xp"] += XP_PER_MESSAGE
        user["messages"] += 1
        check_level_up(message.author.id)
        save_data()

    await bot.process_commands(message)
    
@bot.tree.command(name="leave")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("❌ Not connected.", ephemeral=True)
        return
    await vc.disconnect()
    await interaction.response.send_message("👋 Disconnected.", ephemeral=True)
    
    
@bot.tree.command(name="sounds", description="Open soundboard GUI")
async def sounds(interaction: discord.Interaction):

    base_dir = os.path.dirname(os.path.abspath(__file__))
    sounds_dir = os.path.join(base_dir, "sounds")

    if not os.path.isdir(sounds_dir):
        await interaction.response.send_message(
            "❌ sounds folder not found.",
            ephemeral=True
        )
        return

    sound_files = sorted(
        [f for f in os.listdir(sounds_dir) if f.lower().endswith(".mp3")]
    )

    if not sound_files:
        await interaction.response.send_message(
            "❌ No .mp3 files found in sounds folder.",
            ephemeral=True
        )
        return

    view = SoundBoard(sound_files)

    await interaction.response.send_message(
        f"🎵 **Soundboard** — Page 1 / {view.total_pages}",
        view=view,
        ephemeral=True
    )
    # ---------------- RUN ----------------

if not TOKEN or TOKEN.startswith("PASTE_"):
    raise ValueError(
        "Missing bot token. Set DISCORD_BOT_TOKEN environment variable "
        "or update config.json token."
    )

bot.run(TOKEN)
