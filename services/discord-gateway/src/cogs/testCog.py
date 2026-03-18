from discord.ext import commands
from shared import bblogger

flogger = bblogger.get_logger("test-cog-Test")


class TestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        flogger.debug("Initializing TestCog")

    @commands.command()
    async def test_command(self, ctx):
        await ctx.send("Test command from TestCog")


async def setup(bot):
    await bot.add_cog(TestCog(bot))
