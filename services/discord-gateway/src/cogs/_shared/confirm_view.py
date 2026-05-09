import discord


class ConfirmView(discord.ui.View):
    """Reusable two-button confirmation dialog.

    Usage::

        view = ConfirmView(action="delete all bounties", timeout=60)
        await interaction.followup.send(embed=warning_embed, view=view, ephemeral=True)
        await view.wait()
        if view.result is None:
            # timed out
        elif not view.result:
            # cancelled
        else:
            # confirmed — proceed
    """

    def __init__(self, *, action: str = "this action", timeout: float = 60):
        super().__init__(timeout=timeout)
        self.result: bool | None = None
        self.action = action

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = False
        self.stop()
        await interaction.response.defer()
