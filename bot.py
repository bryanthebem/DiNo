# bot.py (Versão final, com o fluxo do comando /editar totalmente corrigido)

import discord
from discord import app_commands, Interaction, SelectOption, ButtonStyle, Color
from discord.ui import View, Button, Select
from discord.ext import commands
import os
import re
import json
import asyncio
from dotenv import load_dotenv
from notion_integration import NotionIntegration, NotionAPIError
from typing import List, Optional, Dict, Any
from datetime import datetime

# Carregar variáveis de ambiente e inicializar bot/notion...
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
notion = NotionIntegration()

# Funções save_config e load_config...
def save_config(server_id, channel_id, new_channel_config):
    try:
        with open('configs.json', 'r') as f: configs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): configs = {}
    server_config = configs.get(str(server_id), {})
    if 'channels' not in server_config: server_config['channels'] = {}
    channel_config = server_config["channels"].get(str(channel_id), {})
    channel_config.update(new_channel_config)
    server_config["channels"][str(channel_id)] = channel_config
    configs[str(server_id)] = server_config
    if 'notion_url' in channel_config: os.environ["NOTION_URL_CONFIGURADA"] = channel_config['notion_url']
    with open('configs.json', 'w') as f: json.dump(configs, f, indent=4)

def load_config(server_id, channel_id):
    try:
        with open('configs.json', 'r') as f: configs = json.load(f)
        channel_config = configs.get(str(server_id), {}).get("channels", {}).get(str(channel_id))
        if channel_config and 'notion_url' in channel_config: os.environ["NOTION_URL_CONFIGURADA"] = channel_config['notion_url']
        return channel_config
    except FileNotFoundError: return None

# --- CLASSES DE VIEW E MODAL (COMPONENTES DE UI) ---
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

class PaginationView(View):
    def __init__(self, author: discord.Member, results: list, display_properties: list, action: Optional[str] = None, timeout=300.0):
        super().__init__(timeout=timeout)
        self.author, self.results, self.display_properties = author, results, display_properties
        self.current_page, self.total_pages, self.selected_page_id = 0, len(results), None
        if action != 'edit': self.remove_item(self.edit_button)
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
            return False
        return True
    async def get_page_embed(self) -> discord.Embed:
        page_data = self.results[self.current_page]
        embed_data = notion.format_page_for_embed(page_data, fields_inline=False, display_properties=self.display_properties)
        embed = discord.Embed(title=f"📌 {embed_data['title']}", url=embed_data['url'], color=Color.green())
        for field in embed_data['fields']: embed.add_field(name=field['name'], value=field['value'], inline=field['inline'])
        embed.set_footer(text=f"Card {self.current_page + 1} de {self.total_pages}")
        return embed
    def update_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1
    @discord.ui.button(label="⬅️ Anterior", style=ButtonStyle.secondary, row=0)
    async def previous_button(self, interaction: Interaction, button: Button):
        if self.current_page > 0: self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)
    @discord.ui.button(label="Próximo ➡️", style=ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: Interaction, button: Button):
        if self.current_page < self.total_pages - 1: self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)
    @discord.ui.button(label="✏️ Editar este Card", style=ButtonStyle.primary, row=1)
    async def edit_button(self, interaction: Interaction, button: Button):
        self.selected_page_id = self.results[self.current_page]['id']
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

class SearchModal(discord.ui.Modal):
    def __init__(self, config: dict, selected_property: dict):
        self.config, self.selected_property = config, selected_property
        super().__init__(title=f"Buscar por '{self.selected_property['name']}'")
        self.search_term_input = discord.ui.TextInput(label="Digite o termo que você quer procurar", style=discord.TextStyle.short, placeholder="Ex: 'Card de Teste', 'Bug Importante', etc.", required=True)
        self.add_item(self.search_term_input)
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        search_term = self.search_term_input.value
        try:
            cards = notion.search_in_database(self.config['notion_url'], search_term, self.selected_property['name'], self.selected_property['type'])
            results = cards.get('results', [])
            if not results: return await interaction.followup.send(f"❌ Nenhum resultado encontrado para **'{search_term}'**.", ephemeral=True)
            await interaction.followup.send(f"✅ **{len(results)}** resultado(s) encontrado(s)! Veja abaixo:", ephemeral=True)
            view = PaginationView(interaction.user, results, self.config.get('display_properties'))
            view.update_buttons()
            await interaction.channel.send(embed=await view.get_page_embed(), view=view)
        except NotionAPIError as e: await interaction.followup.send(f"❌ **Ocorreu um erro com o Notion:**\n`{e}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"🔴 **Ocorreu um erro inesperado:**\n`{e}`", ephemeral=True)
            print(f"Erro inesperado no on_submit do SearchModal: {e}")

class CardSelectPropertiesView(View):
    def __init__(self, author_id: int, config: dict, all_properties: list, select_props: list, collected_from_modal: dict):
        super().__init__(timeout=300.0)
        self.author_id, self.config, self.all_properties = author_id, config, all_properties
        self.collected_properties = collected_from_modal.copy()
        for prop in select_props:
            prop_name, prop_type = prop['name'], prop['type']
            options = [SelectOption(label=opt) for opt in prop.get('options', [])[:25]]
            is_multi = prop_type == 'multi_select'
            placeholder = "Escolha uma ou mais opções..." if is_multi else "Escolha uma opção..."
            select_menu = Select(placeholder=f"{placeholder} para {prop_name}", options=options, max_values=len(options) if is_multi else 1, min_values=0, custom_id=f"select_{prop_name}")
            select_menu.callback = self.on_select_callback
            self.add_item(select_menu)
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
            return False
        return True
    async def on_select_callback(self, interaction: Interaction):
        select_menu_data = interaction.data
        prop_name = select_menu_data['custom_id'].replace("select_", "")
        values = select_menu_data.get('values', [])
        if len(values) > 1: self.collected_properties[prop_name] = values
        elif values: self.collected_properties[prop_name] = values[0]
        await interaction.response.defer()
    @discord.ui.button(label="✅ Criar Card", style=ButtonStyle.green, row=4)
    async def confirm_button(self, interaction: Interaction, button: Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            title_prop_name = next((p['name'] for p in self.all_properties if p['type'] == 'title'), None)
            if not title_prop_name: raise NotionAPIError("Nenhuma propriedade de Título foi encontrada.")
            title_value = self.collected_properties.pop(title_prop_name, f"Card criado em {datetime.now().strftime('%d/%m')}")
            page_properties = notion.build_page_properties(title_value, self.collected_properties)
            response = notion.insert_into_database(self.config['notion_url'], page_properties)
            for item in self.children: item.disabled = True
            await interaction.edit_original_response(content="✅ Card criado com sucesso!", view=self)
            display_properties_names = self.config.get('display_properties')
            embed_data = notion.format_page_for_embed(response, fields_inline=True, display_properties=display_properties_names)
            success_embed = discord.Embed(title=f"✅ Card '{embed_data['title']}' Criado com Sucesso!", url=embed_data['url'], color=Color.purple())
            for field in embed_data['fields']: success_embed.add_field(name=field['name'], value=field['value'], inline=field['inline'])
            await interaction.channel.send(embed=success_embed)
        except NotionAPIError as e: await interaction.followup.send(f"❌ **Ocorreu um erro com o Notion:**\n`{e}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"🔴 **Ocorreu um erro inesperado:**\n`{e}`", ephemeral=True)
            print(f"Erro inesperado no confirm_button: {e}")

class CardModal(discord.ui.Modal):
    def __init__(self, config: dict, all_properties: list, text_props: list, select_props: list):
        super().__init__(title="Criar Novo Card (Etapa 1 de 2)")
        self.config, self.all_properties, self.text_props, self.select_props = config, all_properties, text_props, select_props
        self.text_inputs = {}
        for prop in self.text_props:
            prop_name, prop_type = prop['name'], prop['type']
            text_style = discord.TextStyle.paragraph if any(k in prop_name.lower() for k in ["desc", "detalhe"]) else discord.TextStyle.short
            text_input = discord.ui.TextInput(label=prop_name, style=text_style, required=prop_type == 'title')
            self.text_inputs[prop_name] = text_input
            self.add_item(text_input)
    async def on_submit(self, interaction: Interaction):
        collected_from_modal = {name: item.value for name, item in self.text_inputs.items() if item.value}
        if not self.select_props:
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                title_prop_name = next((p['name'] for p in self.all_properties if p['type'] == 'title'), None)
                if not title_prop_name: raise NotionAPIError("Propriedade de Título não encontrada.")
                title_value = collected_from_modal.pop(title_prop_name, f"Card criado em {datetime.now().strftime('%d/%m')}")
                page_properties = notion.build_page_properties(title_value, collected_from_modal)
                response = notion.insert_into_database(self.config['notion_url'], page_properties)
                embed_data = notion.format_page_for_embed(response, display_properties=self.config.get('display_properties'))
                success_embed = discord.Embed(title=f"✅ Card '{embed_data['title']}' Criado!", url=embed_data['url'], color=Color.purple())
                for field in embed_data['fields']: success_embed.add_field(name=field['name'], value=field['value'], inline=field['inline'])
                await interaction.channel.send(embed=success_embed)
                await interaction.followup.send("✅ Card criado com sucesso!", ephemeral=True)
            except Exception as e:
                if not interaction.response.is_done(): await interaction.response.send_message(f"🔴 Erro ao criar o card: {e}", ephemeral=True)
                else: await interaction.followup.send(f"🔴 Erro ao criar o card: {e}", ephemeral=True)
        else:
            await interaction.response.send_message("📝 Etapa 1/2 concluída. Agora, selecione os valores para as propriedades abaixo.", ephemeral=True)
            view = CardSelectPropertiesView(interaction.user.id, self.config, self.all_properties, self.select_props, collected_from_modal)
            await interaction.followup.send(view=view, ephemeral=True)

class ContinueEditingView(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180.0)
        self.author_id = author_id
        self.choice = None
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com os botões de outra pessoa.", ephemeral=True)
            return False
        return True
    @discord.ui.button(label="✏️ Editar outra propriedade", style=ButtonStyle.secondary)
    async def continue_editing(self, interaction: Interaction, button: Button):
        self.choice = 'continue'
        await interaction.response.edit_message(content="Continuando edição...", view=None)
        self.stop()
    @discord.ui.button(label="✅ Concluir Edição", style=ButtonStyle.success)
    async def finish_editing(self, interaction: Interaction, button: Button):
        self.choice = 'finish'
        await interaction.response.edit_message(content="Finalizando...", view=None)
        self.stop()

class PublishView(View):
    def __init__(self, author_id: int, embed_to_publish: discord.Embed):
        super().__init__(timeout=300.0)
        self.author_id = author_id
        self.embed = embed_to_publish
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com os botões de outra pessoa.", ephemeral=True)
            return False
        return True
    @discord.ui.button(label="📢 Exibir para Todos", style=ButtonStyle.primary)
    async def publish(self, interaction: Interaction, button: Button):
        button.disabled = True
        await interaction.response.edit_message(content="✅ Card publicado no canal!", view=self)
        await interaction.channel.send(embed=self.embed)
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
    if not notion.extract_database_id(url): return await interaction.followup.send("❌ URL do Notion inválida. Verifique o link.", ephemeral=True)
    try:
        save_config(interaction.guild_id, config_channel_id, {'notion_url': url})
        await interaction.followup.send(f"✅ Banco de dados definido! Agora, vamos selecionar as propriedades.", ephemeral=True)
        all_properties = notion.get_properties_for_interaction(url)
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
        create_props = await run_selection_process("🛠️ Configurar Criação (`/card`)", "Selecione as propriedades que o bot deve perguntar ao criar um card.")
        if create_props is None: return await interaction.followup.send("⌛ Seleção de propriedades de criação cancelada.", ephemeral=True)
        save_config(interaction.guild_id, config_channel_id, {'create_properties': create_props})
        await interaction.followup.send(f"✅ Propriedades para **criação** salvas: `{', '.join(create_props)}`", ephemeral=True)
        display_props = await run_selection_process("🎨 Configurar Exibição (`/busca`)", "Selecione as propriedades que o bot deve mostrar nos resultados de busca.")
        if display_props is None: return await interaction.followup.send("⌛ Seleção de propriedades de exibição cancelada.", ephemeral=True)
        save_config(interaction.guild_id, config_channel_id, {'display_properties': display_props})
        await interaction.followup.send(f"✅ Propriedades para **exibição** salvas: `{', '.join(display_props)}`", ephemeral=True)
        await interaction.followup.send(f"🎉 **Configuração para o canal `#{config_channel.name}` concluída!**", ephemeral=True)
    except NotionAPIError as e: await interaction.followup.send(f"❌ Erro ao acessar o Notion: {e}\n\nVerifique se o link está correto e se o bot tem permissão para acessar o banco de dados.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"🔴 Ocorreu um erro inesperado durante a configuração: {e}", ephemeral=True)
        print(f"Erro inesperado no /config: {e}")

@config_command.error
async def config_command_error(interaction: Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions): message = "❌ Você precisa ser um administrador para usar este comando."
    else: message = f"🔴 Um erro de comando ocorreu: {error}"; print(f"Erro no comando /config: {error}")
    if interaction.response.is_done(): await interaction.followup.send(message, ephemeral=True)
    else: await interaction.response.send_message(message, ephemeral=True)

@bot.tree.command(name="card", description="Abre um formulário para criar um novo card no Notion.")
async def interactive_card(interaction: Interaction):
    try:
        config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
        config = load_config(interaction.guild_id, config_channel_id)
        if not config or 'notion_url' not in config: return await interaction.response.send_message("❌ O Notion não foi configurado para este canal.", ephemeral=True)
        all_properties = notion.get_properties_for_interaction(config['notion_url'])
        create_properties_names = config.get('create_properties')
        if not create_properties_names: return await interaction.response.send_message("❌ As propriedades para criação não foram configuradas.", ephemeral=True)
        properties_to_ask = [prop for prop in all_properties if prop['name'] in create_properties_names]
        text_props = [p for p in properties_to_ask if p['type'] not in ['select', 'multi_select', 'status']]
        select_props = [p for p in properties_to_ask if p['type'] in ['select', 'multi_select', 'status']]
        if len(text_props) > 5: return await interaction.response.send_message(f"❌ O formulário precisa de {len(text_props)} campos de texto, mas o Discord suporta no máximo 5.", ephemeral=True)
        if len(select_props) > 4: return await interaction.response.send_message(f"❌ O formulário precisa de {len(select_props)} menus de seleção, mas o Discord suporta no máximo 4.", ephemeral=True)
        if not properties_to_ask: return await interaction.response.send_message("❌ Nenhuma propriedade foi configurada para criação.", ephemeral=True)
        if not text_props and select_props:
            view = CardSelectPropertiesView(interaction.user.id, config, all_properties, select_props, {})
            return await interaction.response.send_message("Por favor, preencha as propriedades abaixo para criar o card:", view=view, ephemeral=True)
        modal = CardModal(config, all_properties, text_props, select_props)
        await interaction.response.send_modal(modal)
    except NotionAPIError as e:
        if not interaction.response.is_done(): await interaction.response.send_message(f"❌ Erro com o Notion: {e}", ephemeral=True)
        else: await interaction.followup.send(f"❌ Erro com o Notion: {e}", ephemeral=True)
    except Exception as e:
        if not interaction.response.is_done(): await interaction.response.send_message(f"🔴 Erro inesperado: {e}", ephemeral=True)
        else: await interaction.followup.send(f"🔴 Erro inesperado: {e}", ephemeral=True)
        print(f"Erro inesperado no /card: {e}")

@bot.tree.command(name="busca", description="Inicia uma busca interativa de cards no Notion.")
async def interactive_search(interaction: Interaction):
    try:
        config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
        config = load_config(interaction.guild_id, config_channel_id)
        if not config or 'notion_url' not in config: return await interaction.response.send_message("❌ O Notion não foi configurado.", ephemeral=True)
        all_properties = notion.get_properties_for_interaction(config['notion_url'])
        display_properties_names = config.get('display_properties')
        if not display_properties_names: return await interaction.response.send_message("❌ As propriedades para busca não foram configuradas.", ephemeral=True)
        searchable_options = [prop for prop in all_properties if prop['name'] in display_properties_names]
        if not searchable_options: return await interaction.response.send_message("❌ Nenhuma propriedade pesquisável configurada.", ephemeral=True)
        class PropertySelect(Select):
            def __init__(self, searchable_props, author_id):
                self.searchable_props, self.author_id = searchable_props, author_id
                opts = [SelectOption(label=p['name'], description=f"Tipo: {p['type']}") for p in self.searchable_props[:25]]
                super().__init__(placeholder="Escolha uma propriedade para pesquisar...", options=opts)
            async def callback(self, inter: Interaction):
                if inter.user.id != self.author_id: return await inter.response.send_message("Você não pode interagir com o menu de outra pessoa.", ephemeral=True)
                selected_prop_name = self.values[0]
                selected_property = next((p for p in all_properties if p['name'] == selected_prop_name), None)
                if selected_property['type'] in ['select', 'multi_select', 'status']:
                    prop_options = selected_property.get('options', [])
                    class OptionSelect(Select):
                        def __init__(self):
                            opts = [SelectOption(label=opt) for opt in prop_options[:25]]
                            super().__init__(placeholder=f"Escolha uma opção de '{selected_property['name']}'...", options=opts)
                        async def callback(self, sub_inter: Interaction):
                            await sub_inter.response.defer(thinking=True, ephemeral=True)
                            search_term = self.values[0]
                            cards_encontrados = notion.search_in_database(config['notion_url'], search_term, selected_property['name'], selected_property['type'])
                            results = cards_encontrados.get('results', [])
                            if not results: return await sub_inter.followup.send(f"❌ Nenhum resultado encontrado para **'{search_term}'**.", ephemeral=True)
                            await sub_inter.followup.send(f"✅ **{len(results)}** resultado(s) encontrado(s)! Veja abaixo:", ephemeral=True)
                            pagination_view = PaginationView(sub_inter.user, results, display_properties_names)
                            pagination_view.update_buttons()
                            await sub_inter.channel.send(embed=await pagination_view.get_page_embed(), view=pagination_view)
                    view_options = View(timeout=120.0); view_options.add_item(OptionSelect())
                    await inter.response.edit_message(content=f"➡️ Agora, escolha um valor para **{selected_property['name']}**:", view=view_options)
                else:
                    await inter.response.send_modal(SearchModal(config=config, selected_property=selected_property))
        initial_view = View(timeout=180.0)
        initial_view.add_item(PropertySelect(searchable_options, interaction.user.id))
        await interaction.response.send_message("🔎 Escolha no menu abaixo a propriedade para sua busca.", view=initial_view, ephemeral=True)
    except NotionAPIError as e:
        if not interaction.response.is_done(): await interaction.response.send_message(f"❌ Erro com o Notion: {e}", ephemeral=True)
        else: await interaction.followup.send(f"❌ Erro com o Notion: {e}", ephemeral=True)
    except Exception as e:
        if not interaction.response.is_done(): await interaction.response.send_message(f"🔴 Erro inesperado: {e}", ephemeral=True)
        else: await interaction.followup.send(f"🔴 Erro inesperado: {e}", ephemeral=True)
        print(f"Erro inesperado no /busca: {e}")

@bot.tree.command(name="num_cards", description="Mostra o total de cards no banco de dados do canal.")
async def num_cards(interaction: Interaction):
    try:
        config_channel_id = interaction.channel.parent_id if isinstance(interaction.channel, discord.Thread) else interaction.channel.id
        config = load_config(interaction.guild_id, config_channel_id)
        if not config or 'notion_url' not in config: return await interaction.response.send_message("❌ O Notion não foi configurado.", ephemeral=True)
        count = notion.get_database_count(config['notion_url'])
        await interaction.response.send_message(f"📊 O banco de dados deste canal contém **{count}** cards.")
    except NotionAPIError as e: await interaction.response.send_message(f"❌ Erro ao acessar o Notion: {e}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"🔴 Erro inesperado: {e}", ephemeral=True)
        print(f"Erro inesperado no /num_cards: {e}")

@bot.tree.command(name="editar", description="Edita um card existente no Notion.")
@app_commands.describe(busca="O título ou termo para encontrar o card que você quer editar.")
async def edit_command(interaction: Interaction, busca: str):
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        config = load_config(interaction.guild_id, interaction.channel.id if not isinstance(interaction.channel, discord.Thread) else interaction.channel.parent_id)
        if not config or 'notion_url' not in config: return await interaction.followup.send("❌ O Notion não foi configurado.", ephemeral=True)
        
        all_db_props = notion.get_properties_for_interaction(config['notion_url'])
        title_prop_name = next((p['name'] for p in all_db_props if p['type'] == 'title'), None)
        if not title_prop_name: return await interaction.followup.send("❌ Propriedade de título não encontrada.", ephemeral=True)
        
        results = notion.search_in_database(config['notion_url'], busca, title_prop_name, 'title').get('results', [])
        if not results: return await interaction.followup.send(f"❌ Nenhum card encontrado com o termo '{busca}'.", ephemeral=True)
        
        display_properties_names = config.get('display_properties')
        pagination_view = PaginationView(interaction.user, results, display_properties_names, action='edit')
        pagination_view.update_buttons()
        msg = await interaction.followup.send("Encontrei estes cards. Clique em 'Editar' no card desejado:", embed=await pagination_view.get_page_embed(), view=pagination_view, ephemeral=True)
        await pagination_view.wait()

        if not pagination_view.selected_page_id: return await msg.edit(content="⌛ Edição cancelada.", embed=None, view=None)
        
        page_id_to_edit = pagination_view.selected_page_id
        await msg.edit(content=f"✅ Card selecionado! Iniciando modo de edição...", embed=None, view=None)
        
        editable_props = [p for p in all_db_props if p['name'] in config.get('create_properties', [])]

        while True:
            prop_select_view = View(timeout=180.0)
            prop_select = Select(placeholder="Escolha uma propriedade para editar...", options=[SelectOption(label=p['name'], description=f"Tipo: {p['type']}") for p in editable_props[:25]])
            prop_select_view.add_item(prop_select)
            
            prop_msg = await interaction.followup.send("Qual propriedade você quer alterar agora?", view=prop_select_view, ephemeral=True)

            async def property_choice_callback(inter: Interaction):
                await inter.response.defer()
                prop_select_view.stop()
            prop_select.callback = property_choice_callback
            await prop_select_view.wait()
            
            if not prop_select.values:
                await prop_msg.edit(content="⌛ Edição cancelada.", view=None)
                break

            selected_prop_name = prop_select.values[0]
            selected_prop_details = next((p for p in editable_props if p['name'] == selected_prop_name), None)
            
            new_value = None
            prop_type = selected_prop_details['type']
            
            if prop_type in ['select', 'multi_select', 'status']:
                options_view = View(timeout=180.0)
                options_select = Select(placeholder=f"Escolha para {selected_prop_name}", options=[SelectOption(label=opt) for opt in selected_prop_details.get('options', [])[:25]], max_values=len(selected_prop_details.get('options',[])) if prop_type == 'multi_select' else 1)
                async def options_select_callback(inter: Interaction):
                    options_select.view.stop()
                options_select.callback = options_select_callback
                options_view.add_item(options_select)
                await prop_msg.edit(content=f"Qual o novo valor para **{selected_prop_name}**?", view=options_view)
                await options_view.wait()
                if options_select.values:
                    new_value = options_select.values if prop_type == 'multi_select' else options_select.values[0]
            else:
                class EditModal(discord.ui.Modal, title=f"Editar '{selected_prop_name}'"):
                    new_val_input = discord.ui.TextInput(label="Novo valor", style=discord.TextStyle.paragraph)
                    async def on_submit(self, modal_inter: Interaction):
                        self.result = self.new_val_input.value
                        await modal_inter.response.defer()
                        self.stop()
                
                # Botão para abrir o modal
                open_modal_view = View(timeout=180.0)
                open_modal_button = Button(label=f"Clique para Inserir o Valor de '{selected_prop_name}'")
                async def open_modal_callback(inter: Interaction):
                    edit_modal = EditModal()
                    await inter.response.send_modal(edit_modal)
                    await edit_modal.wait()
                    open_modal_view.result = edit_modal.result
                    open_modal_view.stop()
                open_modal_button.callback = open_modal_callback
                open_modal_view.add_item(open_modal_button)
                await prop_msg.edit(content="Use o botão abaixo para editar o valor.", view=open_modal_view)
                await open_modal_view.wait()
                new_value = getattr(open_modal_view, 'result', None)

            if new_value is None:
                await prop_msg.edit(content="❌ Nenhum novo valor fornecido. Tente novamente.", view=None)
                await asyncio.sleep(2)
                await prop_msg.delete()
                continue
            
            await prop_msg.edit(content=f"⚙️ Atualizando propriedade...", view=None)
            properties_payload = notion.build_update_payload(selected_prop_name, prop_type, new_value)
            notion.update_page(page_id_to_edit, properties_payload)
            await prop_msg.edit(content=f"✅ Propriedade **{selected_prop_name}** atualizada!", view=None)
            
            continue_view = ContinueEditingView(interaction.user.id)
            await interaction.followup.send("Deseja continuar editando este card?", view=continue_view, ephemeral=True)
            await continue_view.wait()
            
            if continue_view.choice != 'continue':
                break

        final_page_data = notion.get_page(page_id_to_edit)
        final_embed = notion.format_page_for_embed(final_page_data, display_properties=display_properties_names)
        final_success_embed = discord.Embed(title=f"✅ Card '{final_embed['title']}' Finalizado!", url=final_embed['url'], color=Color.purple())
        for field in final_embed['fields']: final_success_embed.add_field(name=field['name'], value=field['value'], inline=field['inline'])

        publish_view = PublishView(interaction.user.id, final_success_embed)
        await interaction.followup.send("Edição concluída! Veja o resultado final abaixo. Você pode publicar no canal se desejar.", embed=final_success_embed, view=publish_view, ephemeral=True)

    except NotionAPIError as e:
        if not interaction.response.is_done(): await interaction.response.send_message(f"❌ Erro com o Notion: {e}", ephemeral=True)
        else: await interaction.followup.send(f"❌ Erro com o Notion: {e}", ephemeral=True)
    except Exception as e:
        if not interaction.response.is_done(): await interaction.response.send_message(f"🔴 Erro inesperado: {e}", ephemeral=True)
        else: await interaction.followup.send(f"🔴 Erro inesperado: {e}", ephemeral=True)
        print(f"Erro inesperado no /editar: {e}")

# --- INICIAR O BOT ---
if __name__ == "__main__":
    if DISCORD_TOKEN:
        try: bot.run(DISCORD_TOKEN)
        except Exception as e: print(f"Erro fatal ao iniciar o bot: {e}")
    else: print("❌ Token do Discord não encontrado. Verifique seu arquivo .env")