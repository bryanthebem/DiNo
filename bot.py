# bot.py (Versão Final com Comandos de Barra /)

import discord
from discord import app_commands, Interaction, SelectOption, ButtonStyle, Color
from discord.ui import View, Button, Select
from discord.ext import commands
import os
import re
import json
import asyncio
from dotenv import load_dotenv
from notion_integration import NotionIntegration
from typing import List

# Carregar variáveis de ambiente
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")

# --- INICIALIZAÇÃO DO BOT ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- INICIALIZAÇÃO DAS INTEGRAÇÕES ---
notion = NotionIntegration()

# --- FUNÇÕES DE CONFIGURAÇÃO ---
def save_config(server_id, channel_id, new_channel_config):
    try:
        with open('configs.json', 'r') as f:
            configs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        configs = {}
    
    server_config = configs.get(str(server_id), {})
    if 'channels' not in server_config:
        server_config['channels'] = {}
    channel_config = server_config["channels"].get(str(channel_id), {})
    channel_config.update(new_channel_config)
    server_config["channels"][str(channel_id)] = channel_config
    configs[str(server_id)] = server_config
    if 'notion_url' in channel_config:
        os.environ["NOTION_URL_CONFIGURADA"] = channel_config['notion_url']
    with open('configs.json', 'w') as f:
        json.dump(configs, f, indent=4)

def load_config(server_id, channel_id):
    try:
        with open('configs.json', 'r') as f:
            configs = json.load(f)
        channel_config = configs.get(str(server_id), {}).get("channels", {}).get(str(channel_id))
        if channel_config and 'notion_url' in channel_config:
            os.environ["NOTION_URL_CONFIGURADA"] = channel_config['notion_url']
        return channel_config
    except FileNotFoundError:
        return None

# --- CLASSES DE VIEW (COMPONENTES DE UI) ---
class SelectView(View):
    def __init__(self, select_component: Select, author_id: int, timeout=180.0):
        super().__init__(timeout=timeout)
        self.select_component = select_component
        self.author_id = author_id
        self.add_item(self.select_component)
    
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
            return False
        return True

class ConfirmationView(View):
    def __init__(self, author: discord.Member, timeout=60.0):
        super().__init__(timeout=timeout)
        self.value = None
        self.author = author

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Você não pode interagir com os botões de outra pessoa.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmar", style=ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: Interaction, button: Button):
        self.value = True
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancelar", style=ButtonStyle.red, emoji="❌")
    async def cancel(self, interaction: Interaction, button: Button):
        self.value = False
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

# --- EVENTOS DO BOT ---
@bot.event
async def on_ready():
    if DISCORD_GUILD_ID:
        guild = discord.Object(id=DISCORD_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Comandos sincronizados para o servidor {DISCORD_GUILD_ID}.")
    else:
        await bot.tree.sync()
        print("Comandos sincronizados globalmente.")
    
    print(f"✅ {bot.user} está online e pronto para uso!")

# --- COMANDOS DE BARRA (/) ---

@bot.tree.command(name="config", description="(Admin) Configura o bot para usar um banco de dados do Notion neste canal.")
@app_commands.describe(url="O link (URL) completo do banco de dados do Notion.")
@app_commands.checks.has_permissions(administrator=True)
async def config_command(interaction: Interaction, url: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    config_channel = interaction.channel
    if isinstance(interaction.channel, discord.Thread):
        config_channel = interaction.channel.parent
        await interaction.followup.send(f"ℹ️ Detectei que estamos em um tópico. A configuração será aplicada ao canal pai: `#{config_channel.name}`.", ephemeral=True)
    
    config_channel_id = config_channel.id

    if not notion.extract_database_id(url):
        return await interaction.followup.send("❌ URL do Notion inválida.", ephemeral=True)
    
    save_config(interaction.guild_id, config_channel_id, {'notion_url': url})
    await interaction.followup.send(f"✅ Banco de dados definido! Agora, selecione as propriedades.", ephemeral=True)

    all_properties = notion.get_properties_for_interaction(url)
    if not all_properties:
        return await interaction.followup.send("❌ Não consegui buscar as propriedades.", ephemeral=True)

    property_names = [prop['name'] for prop in all_properties]

    async def run_selection_process(prompt_title, prompt_description):
        class MultiSelect(Select):
            def __init__(self):
                opts = [SelectOption(label=name) for name in property_names[:25]]
                super().__init__(placeholder="Escolha as propriedades...", min_values=1, max_values=len(opts), options=opts)
            
            async def callback(self, inter: Interaction):
                self.view.result = self.values
                for item in self.view.children: item.disabled = True
                await inter.response.edit_message(content=f"Seleção para '{prompt_title}' confirmada!", view=self.view)
                self.view.stop()

        view = SelectView(MultiSelect(), author_id=interaction.user.id, timeout=300.0)
        embed = discord.Embed(title=prompt_title, description=prompt_description, color=Color.blue())
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        await view.wait()
        return getattr(view, 'result', None)

    create_props = await run_selection_process("🛠️ Configurar Criação (`/card`)", "Selecione as propriedades que o bot deve perguntar.")
    if create_props is None: return await interaction.followup.send("⌛ Seleção cancelada.", ephemeral=True)
    save_config(interaction.guild_id, config_channel_id, {'create_properties': create_props})
    await interaction.followup.send(f"✅ Propriedades para **criação** salvas: `{', '.join(create_props)}`", ephemeral=True)

    display_props = await run_selection_process("🎨 Configurar Exibição (`/busca`)", "Selecione as propriedades que o bot deve mostrar.")
    if display_props is None: return await interaction.followup.send("⌛ Seleção cancelada.", ephemeral=True)
    save_config(interaction.guild_id, config_channel_id, {'display_properties': display_props})
    await interaction.followup.send(f"✅ Propriedades para **exibição** salvas: `{', '.join(display_props)}`", ephemeral=True)
    
    await interaction.followup.send(f"🎉 **Configuração para o canal `#{config_channel.name}` concluída!**", ephemeral=True)

@config_command.error
async def config_command_error(interaction: Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Você precisa ser um administrador para usar este comando."
    else:
        message = f"🔴 Um erro ocorreu: {error}"
        print(f"Erro no comando /config: {error}")
    
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="card", description="Inicia uma conversa para criar um novo card no Notion.")
async def interactive_card(interaction: Interaction):
    config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
    config = load_config(interaction.guild_id, config_channel_id)
    if not config or 'notion_url' not in config:
        return await interaction.response.send_message("❌ O Notion não foi configurado para este canal. Use `/config`.", ephemeral=True)
    
    await interaction.response.send_message(f"🤖 Olá, {interaction.user.mention}! Vamos criar um novo card. Responda às perguntas a seguir no canal.", ephemeral=True)

    create_properties_names = config.get('create_properties')
    if not create_properties_names:
        return await interaction.followup.send("❌ As propriedades para criação não foram configuradas. Use `/config`.", ephemeral=True)

    all_properties = notion.get_properties_for_interaction(config['notion_url'])
    properties_to_ask = [prop for prop in all_properties if prop['name'] in create_properties_names]
    collected_properties = {}

    for prop in properties_to_ask:
        prop_name, prop_type, prop_options = prop['name'], prop['type'], prop['options']
        user_input = None
        def message_check(message):
            return message.author.id == interaction.user.id and message.channel.id == interaction.channel.id

        if prop_type in ['select', 'multi_select', 'status'] and prop_options:
            select_opts = [SelectOption(label=opt) for opt in prop_options[:25]]
            
            class OptionSelect(Select):
                def __init__(self):
                    super().__init__(placeholder=f"Escolha uma opção para {prop_name}...", options=select_opts)
                async def callback(self, inter: Interaction):
                    self.view.result = self.values[0]
                    for item in self.view.children: item.disabled = True
                    await inter.response.edit_message(content=f"Opção para **{prop_name}** selecionada!", view=self.view)
                    self.view.stop()
            
            view = SelectView(OptionSelect(), author_id=interaction.user.id, timeout=300.0)
            await interaction.channel.send(f"➡️ {interaction.user.mention}, escolha o valor para **{prop_name}**:", view=view)
            await view.wait()
            user_input = getattr(view, 'result', None)
        else:
            await interaction.channel.send(f"➡️ {interaction.user.mention}, qual o valor para **{prop_name}**? (Tipo: `{prop_type}`)")
            try:
                msg = await bot.wait_for('message', timeout=300.0, check=message_check)
                user_input = msg.content
                await msg.add_reaction('✅')
            except asyncio.TimeoutError:
                pass

        if user_input is None:
            return await interaction.channel.send("⌛ Processo cancelado por falta de resposta ou tempo esgotado.")
        
        collected_properties[prop_name] = user_input

    confirm_embed = discord.Embed(title="⚙️ Confirmar Criação", description="Verifique os dados e clique para confirmar.", color=Color.orange())
    for name, value in collected_properties.items():
        confirm_embed.add_field(name=name, value=value, inline=True)
    
    view = ConfirmationView(author=interaction.user)
    await interaction.channel.send(embed=confirm_embed, view=view)
    await view.wait()

    if view.value:
        await interaction.channel.send("📝 Processando a criação do card...")
        title_prop_name = [p['name'] for p in all_properties if p['type'] == 'title'][0]
        title_value = collected_properties.pop(title_prop_name, "Título Padrão")
        page_properties = notion.build_page_properties(title_value, collected_properties)
        response = notion.insert_into_database(config['notion_url'], page_properties)
        
        if isinstance(response, dict) and response.get("object") == "page":
            display_properties_names = config.get('display_properties')
            embed_data = notion.format_page_for_embed(response, fields_inline=True, display_properties=display_properties_names)
            success_embed = discord.Embed(title=f"✅ Card '{embed_data['title']}' Criado com Sucesso!", url=embed_data['url'], color=Color.purple())
            for field in embed_data['fields']:
                success_embed.add_field(name=field['name'], value=field['value'], inline=field['inline'])
            await interaction.channel.send(embed=success_embed)
        else:
            await interaction.channel.send(f"❌ Erro ao criar o card no Notion: `{response}`")
    elif view.value is False:
        await interaction.channel.send("❌ Criação do card cancelada.")

@bot.tree.command(name="busca", description="Inicia uma busca interativa de cards no Notion.")
async def interactive_search(interaction: Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
    config = load_config(interaction.guild_id, config_channel_id)
    if not config or 'notion_url' not in config:
        return await interaction.followup.send("❌ O Notion não foi configurado para este canal. Use `/config`.", ephemeral=True)

    all_properties = notion.get_properties_for_interaction(config['notion_url'])
    if not all_properties:
        return await interaction.followup.send("❌ Não consegui encontrar as propriedades do banco de dados.", ephemeral=True)
        
    display_properties_names = config.get('display_properties')
    if not display_properties_names:
        return await interaction.followup.send("❌ As propriedades para busca não foram configuradas. Use `/config`.", ephemeral=True)
    
    searchable_options = [prop for prop in all_properties if prop['name'] in display_properties_names]
    if not searchable_options:
        return await interaction.followup.send("❌ Nenhuma propriedade pesquisável configurada.", ephemeral=True)

    class PropertySelect(Select):
        def __init__(self):
            opts = [SelectOption(label=p['name'], description=f"Tipo: {p['type']}") for p in searchable_options[:25]]
            super().__init__(placeholder="Escolha uma propriedade para pesquisar...", options=opts)
        
        async def callback(self, inter: Interaction):
            self.view.result = self.values[0]
            for item in self.view.children: item.disabled = True
            await inter.response.edit_message(content=f"Propriedade selecionada: `{self.view.result}`", view=self.view)
            self.view.stop()

    view = SelectView(PropertySelect(), author_id=interaction.user.id, timeout=60.0)
    await interaction.followup.send("🔎 Escolha no menu abaixo a propriedade para sua busca.", view=view, ephemeral=True)
    await view.wait()
    
    selected_prop_name = getattr(view, 'result', None)
    if not selected_prop_name:
        return 
        
    selected_property = next((p for p in all_properties if p['name'] == selected_prop_name), None)
    search_term = None

    if selected_property['type'] in ['select', 'multi_select', 'status'] and selected_property['options']:
        prop_options = selected_property['options']
        
        class OptionSelect(Select):
            def __init__(self):
                opts = [SelectOption(label=opt) for opt in prop_options[:25]]
                super().__init__(placeholder=f"Escolha uma opção de '{selected_property['name']}'...", options=opts)
            async def callback(self, inter: Interaction):
                self.view.result = self.values[0]
                for item in self.view.children: item.disabled = True
                await inter.response.edit_message(content=f"Opção de busca: `{self.view.result}`", view=self.view)
                self.view.stop()

        view_options = SelectView(OptionSelect(), author_id=interaction.user.id, timeout=120.0)
        await interaction.followup.send(f"➡️ Agora, escolha um valor para **{selected_property['name']}**:", view=view_options, ephemeral=True)
        await view_options.wait()
        search_term = getattr(view_options, 'result', None)
    else:
        await interaction.followup.send(f"✅ Você selecionou **{selected_property['name']}**. Agora, digite o que você quer procurar no canal:", ephemeral=True)
        def message_check(message):
            return message.author.id == interaction.user.id and message.channel.id == interaction.channel.id
        try:
            search_term_msg = await bot.wait_for('message', timeout=120.0, check=message_check)
            search_term = search_term_msg.content
        except asyncio.TimeoutError:
            pass

    if not search_term:
        return await interaction.followup.send("⌛ Busca cancelada por falta de resposta.", ephemeral=True)

    await interaction.followup.send(f"🔎 Buscando por **'{search_term}'**...", ephemeral=True)
    cards_encontrados = notion.search_in_database(config['notion_url'], search_term, selected_property['name'], selected_property['type'])
    
    results = cards_encontrados.get('results', [])
    if not results:
        return await interaction.followup.send(f"❌ Nenhum resultado encontrado para **'{search_term}'**.", ephemeral=True)
    
    await interaction.followup.send(f"✅ **{len(results)}** resultado(s) encontrado(s)! Veja abaixo:", ephemeral=True)
    for result in results:
        embed_data = notion.format_page_for_embed(result, fields_inline=False, display_properties=display_properties_names)
        result_embed = discord.Embed(title=f"📌 {embed_data['title']}", url=embed_data['url'], color=Color.green())
        for field in embed_data['fields']:
            result_embed.add_field(name=field['name'], value=field['value'], inline=field['inline'])
        await interaction.channel.send(embed=result_embed)

@bot.tree.command(name="num_cards", description="Mostra o total de cards no banco de dados do canal.")
async def num_cards(interaction: Interaction):
    config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
    config = load_config(interaction.guild_id, config_channel_id)
    if not config or 'notion_url' not in config:
        return await interaction.response.send_message("❌ O Notion não foi configurado para este canal.", ephemeral=True)
    
    count = notion.get_database_count(config['notion_url'])
    if isinstance(count, int):
        await interaction.response.send_message(f"📊 O banco de dados deste canal contém **{count}** cards.")
    else:
        await interaction.response.send_message(f"❌ Erro ao contar os cards: {count}", ephemeral=True)

# --- INICIAR O BOT ---
if __name__ == "__main__":
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("❌ Token do Discord não encontrado. Verifique seu arquivo .env")