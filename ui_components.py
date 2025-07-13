# ui_components.py (Versão Final, Completa e Integrada)

import discord
from discord import Interaction, SelectOption, ButtonStyle, Color
from discord.ui import View, Button, Select, Modal, TextInput
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

# Módulos locais
from notion_integration import NotionIntegration, NotionAPIError
from config_utils import save_config, load_config
from ia_processor import summarize_thread_content

# --- CLASSES PARA O FLUXO DE REGRAS DE NOTIFICAÇÃO ---

class RuleValueInputModal(Modal, title="Definir Valor do Gatilho"):
    """Modal para o usuário digitar o valor de gatilho para a regra."""
    value_input = TextInput(
        label="Valor do Gatilho",
        placeholder="Digite o valor exato que deve acionar a regra...",
        style=discord.TextStyle.short,
        required=True)

    def __init__(self, rule_data: dict,
                 view_to_resume: 'NotificationRuleWizard'):
        super().__init__(timeout=300.0)
        self.rule_data = rule_data
        self.view_to_resume = view_to_resume
        self.value_input.label = f"Valor para a propriedade '{self.rule_data['trigger_property_name']}'"

    async def on_submit(self, interaction: Interaction):
        self.rule_data['trigger_value_name'] = self.value_input.value
        await self.view_to_resume.on_value_defined(interaction)

class RuleMessageModal(Modal, title="Mensagem da Notificação"):
    """Modal para o usuário definir a mensagem customizada da regra."""
    message_template_input = TextInput(
        label="Template da Mensagem",
        style=discord.TextStyle.paragraph,
        placeholder="Use {card_title} e {trigger_value}",
        required=True,
        default="✅ O status do card '{card_title}' foi atualizado para '{trigger_value}'.")

    def __init__(self, rule_data: dict, guild_id: int, channel_id: int, wizard_view: 'NotificationRuleWizard'):
        super().__init__(timeout=300.0)
        self.rule_data = rule_data
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.wizard_view = wizard_view

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        self.rule_data['message_template'] = self.message_template_input.value

        full_config = load_config(self.guild_id, self.channel_id) or {}
        notification_rules = full_config.get('notification_rules', [])
        notification_rules.append(self.rule_data)
        save_config(self.guild_id, self.channel_id, {'notification_rules': notification_rules})

        embed = discord.Embed(title="✅ Nova Regra de Notificação Criada!", color=Color.green())
        embed.add_field(name="Propriedade Gatilho", value=self.rule_data.get('trigger_property_name', 'N/A'), inline=False)
        embed.add_field(name="Valor do Gatilho", value=self.rule_data.get('trigger_value_name', 'N/A'), inline=False)
        action_text = {
            "send_to_topic": "Enviar no Tópico do Card",
            "send_to_channel": "Enviar no Canal Principal",
            "dm_responsible": f"Enviar DM para '{self.rule_data.get('responsible_person_prop', 'N/A')}'"
        }.get(self.rule_data.get('action_type'), "Ação Desconhecida")
        embed.add_field(name="Ação", value=action_text, inline=False)
        embed.add_field(name="Mensagem", value=f"```{self.rule_data.get('message_template')}```", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
        await self.wizard_view.finalize_rule_creation()


class NotificationRuleWizard(View):
    """
    Assistente passo a passo que agora inclui a seleção do tipo de ação.
    """
    def __init__(self, author_id: int, guild_id: int, channel_id: int,
                 notion: NotionIntegration, config: dict, parent_view: 'NotificationConfigView'):
        super().__init__(timeout=300.0)
        self.author_id = author_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.notion = notion
        self.config = config
        self.parent_view = parent_view # Para atualizar a view principal no final
        self.all_props = self.notion.get_properties_for_interaction(self.config['notion_url'])
        self.rule_data = {'rule_id': str(uuid.uuid4())}

        self.show_step1_select_property()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Você não pode interagir com o assistente de outra pessoa.", ephemeral=True)
            return False
        return True

    # PASSO 1: Escolher Propriedade
    def show_step1_select_property(self):
        self.clear_items()
        options = [SelectOption(label=p['name'], value=p['name'], description=f"Tipo: {p['type']}") for p in self.all_props]
        prop_select = Select(placeholder="Passo 1: Escolha a propriedade de gatilho...", options=options)
        prop_select.callback = self.on_property_selected
        self.add_item(prop_select)

    async def on_property_selected(self, interaction: Interaction):
        selected_prop_name = interaction.data['values'][0]
        self.rule_data['trigger_property_name'] = selected_prop_name
        prop_details = next((p for p in self.all_props if p['name'] == selected_prop_name), None)
        
        if prop_details and prop_details.get('type') in ['status', 'select', 'multi_select']:
            if not prop_details.get('options'):
                return await interaction.response.edit_message(
                    content="❌ Erro: Esta propriedade não tem opções configuradas no Notion.",
                    view=None)
            await self.show_step2_select_value(interaction, prop_details.get('options', []))
        else:
            await self.show_step2_input_value(interaction)

    # PASSO 2: Escolher Valor
    async def show_step2_select_value(self, interaction: Interaction, options: list):
        self.clear_items()
        select_options = [SelectOption(label=opt, value=opt) for opt in options[:25]]
        value_select = Select(
            placeholder="Passo 2: Escolha o valor que dispara a notificação...",
            options=select_options)
        value_select.callback = self.on_value_selected
        self.add_item(value_select)
        await interaction.response.edit_message(view=self)

    async def show_step2_input_value(self, interaction: Interaction):
        value_modal = RuleValueInputModal(self.rule_data, self)
        await interaction.response.send_modal(value_modal)

    async def on_value_selected(self, interaction: Interaction):
        self.rule_data['trigger_value_name'] = interaction.data['values'][0]
        await self.show_step3_select_action(interaction)

    async def on_value_defined(self, interaction: Interaction):
        await self.show_step3_select_action(interaction)

    # PASSO 3: Escolher Ação
    async def show_step3_select_action(self, interaction: Interaction):
        self.clear_items()
        action_select = Select(
            placeholder="Passo 3: Escolha a ação a ser executada...",
            options=[
                SelectOption(label="Enviar no Tópico do Card", value="send_to_topic", description="Envia a notificação no tópico salvo no card."),
                SelectOption(label="Enviar no Canal Principal", value="send_to_channel", description="Envia no canal onde o comando /config foi usado."),
                SelectOption(label="Enviar DM para Responsável", value="dm_responsible", description="Envia uma DM para o usuário na prop. 'Pessoa'.")
            ]
        )
        action_select.callback = self.on_action_selected
        self.add_item(action_select)
        
        # Se a interação já foi respondida (vindo de um modal), edita a mensagem original
        # Se não (vindo de um Select), usa followup para criar a mensagem
        if interaction.response.is_done():
            # A interação do modal já tem um `defer` ou `send_message`, então usamos `edit_original_response` na interação do wizard
            await self.parent_view.original_interaction.edit_original_response(content="**Assistente de Criação de Regra**\nAgora, escolha o que essa regra deve fazer.", view=self)
        else:
            await interaction.response.edit_message(content="**Assistente de Criação de Regra**\nAgora, escolha o que essa regra deve fazer.", view=self)


    async def on_action_selected(self, interaction: Interaction):
        action_type = interaction.data['values'][0]
        self.rule_data['action_type'] = action_type

        if action_type == 'dm_responsible':
            await self.show_step4_select_person_prop(interaction)
        else:
            await self.show_step5_define_message(interaction)

    # PASSO 4 (Condicional): Escolher Propriedade de Pessoa
    async def show_step4_select_person_prop(self, interaction: Interaction):
        self.clear_items()
        people_props = [p for p in self.all_props if p['type'] == 'people']
        if not people_props:
            await interaction.response.edit_message(content="❌ Nenhuma propriedade do tipo 'Pessoa' encontrada para enviar DMs. A regra não pode ser criada.", view=None)
            return

        person_select = Select(
            placeholder="Passo 4: Qual prop. de 'Pessoa' contém o responsável?",
            options=[SelectOption(label=p['name'], value=p['name']) for p in people_props]
        )
        person_select.callback = self.on_person_prop_selected
        self.add_item(person_select)
        await interaction.response.edit_message(view=self)

    async def on_person_prop_selected(self, interaction: Interaction):
        self.rule_data['responsible_person_prop'] = interaction.data['values'][0]
        await self.show_step5_define_message(interaction)

    # PASSO 5: Definir a Mensagem
    async def show_step5_define_message(self, interaction: Interaction):
        message_modal = RuleMessageModal(
            rule_data=self.rule_data,
            guild_id=self.guild_id,
            channel_id=self.channel_id,
            wizard_view=self
        )
        await interaction.response.send_modal(message_modal)
        self.stop()

    async def finalize_rule_creation(self):
        """Chamado pelo modal para atualizar a view principal."""
        await self.parent_view.update_after_rule_change()


class NotificationConfigView(View):
    """
    A tela principal para gerenciar as regras. Mostra as regras existentes
    e botões para adicionar ou excluir.
    """
    def __init__(self, guild_id: int, channel_id: int, config: dict,
                 notion: NotionIntegration, interaction: Interaction):
        super().__init__(timeout=300.0)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.config = config
        self.notion = notion
        self.original_interaction = interaction
        self.update_view()

    def update_view(self):
        """Atualiza a view com base nas regras atuais do arquivo de configuração."""
        self.clear_items()
        self.add_item(self.add_rule_button)

        rules = self.config.get('notification_rules', [])
        if rules:
            options = []
            for rule in rules:
                action_text = {
                    "send_to_topic": "TÓPICO",
                    "send_to_channel": "CANAL",
                    "dm_responsible": "DM"
                }.get(rule.get('action_type'), "???")

                label = f"[{action_text}] Se '{rule.get('trigger_property_name', 'N/A')}' for '{rule.get('trigger_value_name', 'N/A')}'"
                label = label[:100]

                options.append(SelectOption(
                    label=label,
                    value=rule.get('rule_id'),
                    description=f"ID: {rule.get('rule_id', 'N/A')[:8]}..."
                ))

            delete_select = Select(
                placeholder="Selecione uma regra para excluir...",
                options=options
            )
            delete_select.callback = self.delete_rule
            self.add_item(delete_select)

        self.add_item(self.close_button)

    async def update_after_rule_change(self):
        """Recarrega a config do arquivo e atualiza a view para refletir adições/exclusões."""
        self.config = load_config(self.guild_id, self.channel_id) or self.config
        self.update_view()
        try:
            await self.original_interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass

    @discord.ui.button(label="➕ Adicionar Nova Regra", style=ButtonStyle.success, row=0)
    async def add_rule_button(self, interaction: Interaction, button: Button):
        """Inicia o assistente de criação de regras."""
        wizard_view = NotificationRuleWizard(
            author_id=interaction.user.id,
            guild_id=self.guild_id,
            channel_id=self.channel_id,
            notion=self.notion,
            config=self.config,
            parent_view=self
        )
        await interaction.response.edit_message(
            content="**Assistente de Criação de Regra**\nSiga os passos abaixo.",
            view=wizard_view
        )

    async def delete_rule(self, interaction: Interaction):
        """Callback para o menu de exclusão de regras."""
        rule_id_to_delete = interaction.data['values'][0]
        rules = self.config.get('notification_rules', [])
        
        rule_to_delete = next((rule for rule in rules if rule.get('rule_id') == rule_id_to_delete), None)
        
        if not rule_to_delete:
            await interaction.response.send_message("❌ Erro: A regra selecionada não foi encontrada.", ephemeral=True)
            return

        new_rules = [rule for rule in rules if rule.get('rule_id') != rule_id_to_delete]
        save_config(self.guild_id, self.channel_id, {'notification_rules': new_rules})
        
        await interaction.response.defer()
        await interaction.followup.send(
            f"✅ Regra para '{rule_to_delete.get('trigger_property_name')}' foi excluída com sucesso.",
            ephemeral=True
        )
        
        await self.update_after_rule_change()

    @discord.ui.button(label="Fechar", style=ButtonStyle.secondary, row=4)
    async def close_button(self, interaction: Interaction, button: Button):
        """Fecha a view de gerenciamento de regras."""
        await interaction.message.delete()


# --- FUNÇÕES E CLASSES EXISTENTES ---


async def get_topic_participants(thread: discord.Thread,
                                 limit: int = 100) -> set[discord.Member]:
    participants = set()
    async for message in thread.history(limit=limit):
        if not message.author.bot:
            participants.add(message.author)
    return participants


async def get_thread_attachments(thread: discord.Thread,
                                 limit: int = 100) -> List[Dict[str, str]]:
    attachments_data = []
    async for message in thread.history(limit=limit):
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type.startswith(
                    ('image/',
                     'video/')) or attachment.filename.lower().endswith(
                         ('.gif')):
                    attachments_data.append({
                        "type":
                        attachment.content_type.split('/')[0],
                        "url":
                        attachment.url,
                        "filename":
                        attachment.filename
                    })
    return attachments_data


async def _build_notion_page_content(
        config: dict, thread_context: Optional[discord.Thread],
        notion_integration: NotionIntegration) -> Optional[List[Dict]]:
    page_content = []
    if not thread_context: return None
    if config.get('ai_summary_enabled'):
        messages = [msg async for msg in thread_context.history(limit=100)]
        if messages:
            summary_text = await summarize_thread_content(messages)
            if summary_text and not summary_text.startswith("Erro:"):
                page_content.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{
                            "type": "text",
                            "text": {
                                "content": "🤖 Resumo da IA"
                            }
                        }]
                    }
                })
                parsed_summary_blocks = notion_integration._parse_summary_to_notion_blocks(
                    summary_text)
                page_content.extend(parsed_summary_blocks)
    attachments = await get_thread_attachments(thread_context)
    if attachments:
        if page_content:
            page_content.append({
                "object": "block",
                "type": "divider",
                "divider": {}
            })
        page_content.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{
                    "type": "text",
                    "text": {
                        "content": "📎 Anexos do Tópico"
                    }
                }]
            }
        })
        for att in attachments:
            if att['type'] == 'image':
                page_content.append({
                    "object": "block",
                    "type": "image",
                    "image": {
                        "type": "external",
                        "external": {
                            "url": att['url']
                        }
                    }
                })
            elif att['type'] == 'video':
                page_content.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {
                                "content": f"Vídeo/GIF ({att['filename']}): "
                            }
                        }, {
                            "type": "text",
                            "text": {
                                "content": att['url'],
                                "link": {
                                    "url": att['url']
                                }
                            }
                        }]
                    }
                })
    return page_content if page_content else None


async def start_editing_flow(interaction: Interaction, page_id_to_edit: str,
                             config: dict, notion: NotionIntegration):
    await interaction.followup.send("A funcionalidade de edição ainda não foi totalmente implementada.", ephemeral=True)
    pass


class SelectView(View):

    def __init__(self,
                 select_component: Select,
                 author_id: int,
                 timeout=180.0):
        super().__init__(timeout=timeout)
        self.select_component, self.author_id = select_component, author_id
        self.add_item(self.select_component)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Você não pode interagir com o menu de outra pessoa.",
                ephemeral=True)
            return False
        return True


class CardActionView(View):

    def __init__(self, author_id: int, page_id: str, config: dict,
                 notion: NotionIntegration):
        super().__init__(timeout=None)
        self.author_id, self.page_id, self.config, self.notion = author_id, page_id, config, notion

    @discord.ui.button(label="✏️ Editar", style=ButtonStyle.secondary)
    async def edit_button(self, interaction: Interaction, button: Button):
        await interaction.response.send_message(
            "Iniciando modo de edição para este card...", ephemeral=True)
        await start_editing_flow(interaction, self.page_id, self.config,
                                 self.notion)

    @discord.ui.button(label="🗑️ Excluir", style=ButtonStyle.danger)
    async def delete_button(self, interaction: Interaction, button: Button):
        confirm_view = View(timeout=60.0)
        yes_button, no_button = Button(label="Sim, excluir!",
                                       style=ButtonStyle.danger), Button(
                                           label="Cancelar",
                                           style=ButtonStyle.secondary)
        confirm_view.add_item(yes_button)
        confirm_view.add_item(no_button)

        async def yes_callback(inter: Interaction):
            confirm_view.stop()
            try:
                await inter.response.defer(ephemeral=True, thinking=True)
                self.notion.delete_page(self.page_id)
                for item in self.children:
                    item.disabled = True
                original_embed = interaction.message.embeds[0]
                original_embed.title = f"[EXCLUÍDO] {original_embed.title}"
                original_embed.color = Color.dark_gray()
                original_embed.description = "Este card foi excluído."
                await interaction.message.edit(embed=original_embed, view=self)
                await inter.followup.send("✅ Card excluído com sucesso!",
                                          ephemeral=True)
            except Exception as e:
                await inter.followup.send(f"🔴 Erro ao excluir o card: {e}",
                                          ephemeral=True)

        async def no_callback(inter: Interaction):
            confirm_view.stop()
            await inter.response.edit_message(content="❌ Exclusão cancelada.",
                                              view=None)

        yes_button.callback, no_button.callback = yes_callback, no_callback
        await interaction.response.send_message(
            "⚠️ **Você tem certeza que deseja excluir este card?**",
            view=confirm_view,
            ephemeral=True)


class PaginationView(View):

    def __init__(self,
                 author: discord.Member,
                 results: list,
                 config: dict,
                 notion: NotionIntegration,
                 actions: List[str] = []):
        super().__init__(timeout=300.0)
        self.author, self.results, self.config, self.actions, self.notion = author, results, config, actions, notion
        self.current_page, self.total_pages = 0, len(results)
        if 'edit' not in self.actions: self.remove_item(self.edit_button)
        if 'delete' not in self.actions: self.remove_item(self.delete_button)
        if 'share' not in self.actions: self.remove_item(self.share_button)
        self.update_nav_buttons()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Você não pode interagir com os botões de outra pessoa.",
                ephemeral=True)
            return False
        return True

    def update_nav_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

    async def get_page_embed(self) -> discord.Embed:
        page_data = self.results[self.current_page]
        embed = self.notion.format_page_for_embed(
            page_result=page_data,
            display_properties=self.config.get('display_properties', []),
            include_footer=True)
        embed.set_footer(
            text=f"Card {self.current_page + 1} de {self.total_pages}")
        return embed

    @discord.ui.button(label="⬅️", style=ButtonStyle.secondary, row=0)
    async def previous_button(self, interaction: Interaction, button: Button):
        if self.current_page > 0: self.current_page -= 1
        self.update_nav_buttons()
        await interaction.response.edit_message(embed=await
                                                self.get_page_embed(),
                                                view=self)

    @discord.ui.button(label="➡️", style=ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: Interaction, button: Button):
        if self.current_page < self.total_pages - 1: self.current_page += 1
        self.update_nav_buttons()
        await interaction.response.edit_message(embed=await
                                                self.get_page_embed(),
                                                view=self)

    @discord.ui.button(label="✏️ Editar", style=ButtonStyle.primary, row=1)
    async def edit_button(self, interaction: Interaction, button: Button):
        await interaction.response.send_message(f"Iniciando modo de edição...",
                                                ephemeral=True)
        await start_editing_flow(interaction,
                                 self.results[self.current_page]['id'],
                                 self.config, self.notion)

    @discord.ui.button(label="🗑️ Excluir", style=ButtonStyle.danger, row=1)
    async def delete_button(self, interaction: Interaction, button: Button):
        page_id = self.results[self.current_page]['id']
        confirm_view = View(timeout=60.0)
        yes_button, no_button = Button(label="Sim, excluir!",
                                       style=ButtonStyle.danger), Button(
                                           label="Cancelar",
                                           style=ButtonStyle.secondary)
        confirm_view.add_item(yes_button)
        confirm_view.add_item(no_button)

        async def yes_callback(inter: Interaction):
            await inter.response.defer(ephemeral=True, thinking=True)
            try:
                self.notion.delete_page(page_id)
                await interaction.edit_original_response(
                    content="✅ Card excluído com sucesso.",
                    view=None,
                    embed=None)
                await inter.followup.send("Confirmado!", ephemeral=True)
            except Exception as e:
                await inter.followup.send(f"🔴 Erro ao excluir o card: {e}",
                                          ephemeral=True)

        async def no_callback(inter: Interaction):
            await inter.response.edit_message(content="❌ Exclusão cancelada.",
                                              view=None)

        yes_button.callback, no_button.callback = yes_callback, no_callback
        await interaction.response.send_message(
            "⚠️ **Você tem certeza que deseja excluir este card?**",
            view=confirm_view,
            ephemeral=True)

    @discord.ui.button(label="📢 Exibir para Todos",
                       style=ButtonStyle.success,
                       row=2)
    async def share_button(self, interaction: Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        page_data = self.results[self.current_page]
        share_embed = self.notion.format_page_for_embed(
            page_result=page_data,
            display_properties=self.config.get('display_properties', []))
        if share_embed:
            action_view = CardActionView(
                interaction.user.id, page_data['id'],
                self.config, self.notion) if self.config.get(
                    'action_buttons_enabled', True) else None
            await interaction.channel.send(
                f"{interaction.user.mention} compartilhou este card:",
                embed=share_embed,
                view=action_view)
            await interaction.followup.send("✅ Card exibido no canal!",
                                            ephemeral=True)
        else:
            await interaction.followup.send(
                "❌ Não foi possível gerar o embed para compartilhar.",
                ephemeral=True)


class SearchModal(discord.ui.Modal):

    def __init__(self, notion: NotionIntegration, config: dict,
                 selected_property: dict):
        self.notion, self.config, self.selected_property = notion, config, selected_property
        super().__init__(
            title=f"Buscar por '{self.selected_property['name']}'")
        self.search_term_input = discord.ui.TextInput(
            label="Digite o termo que você quer procurar", required=True)
        self.add_item(self.search_term_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            cards = self.notion.search_in_database(
                self.config['notion_url'], self.search_term_input.value,
                self.selected_property['name'], self.selected_property['type'])
            results = cards.get('results', [])
            if not results:
                return await interaction.followup.send(
                    f"❌ Nenhum resultado para **'{self.search_term_input.value}'**.",
                    ephemeral=True)
            await interaction.followup.send(
                f"✅ **{len(results)}** resultado(s) encontrado(s)! Veja abaixo:",
                ephemeral=True)
            view = PaginationView(interaction.user,
                                  results,
                                  self.config,
                                  self.notion,
                                  actions=['edit', 'delete', 'share'])
            view.update_nav_buttons()
            await interaction.followup.send(embed=await view.get_page_embed(),
                                            view=view,
                                            ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"🔴 **Erro:**\n`{e}`",
                                            ephemeral=True)


class PublishView(View):

    def __init__(self, author_id: int, embed_to_publish: discord.Embed,
                 page_id: str, config: dict, notion: NotionIntegration):
        super().__init__(timeout=300.0)
        self.author_id, self.embed, self.page_id, self.config, self.notion = author_id, embed_to_publish, page_id, config, notion

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Você não pode interagir com o menu de outra pessoa.",
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📢 Exibir para Todos", style=ButtonStyle.primary)
    async def publish(self, interaction: Interaction, button: Button):
        button.disabled = True
        await interaction.response.edit_message(
            content="✅ Card publicado no canal!", view=self)
        action_view = CardActionView(self.author_id, self.page_id, self.config,
                                     self.notion) if self.config.get(
                                         'action_buttons_enabled',
                                         True) else None
        await interaction.channel.send(embed=self.embed, view=action_view)
        self.stop()


# Em ui_components.py, substitua esta classe:

class CardSelectPropertiesView(View):

    def __init__(self, author_id: int, config: dict, all_properties: list,
                 select_props: list, collected_from_modal: dict,
                 thread_context: Optional[discord.Thread],
                 notion: NotionIntegration):
        super().__init__(timeout=300.0)
        self.author_id = author_id
        self.config = config
        self.all_properties = all_properties
        self.select_props = select_props
        self.collected_properties = collected_from_modal.copy()
        self.thread_context = thread_context
        self.notion = notion
        
        for prop in self.select_props:
            is_multi = prop['type'] == 'multi_select'
            select_menu = Select(
                placeholder=f"Escolha para {prop['name']}",
                options=[
                    SelectOption(label=opt)
                    for opt in prop.get('options', [])[:25]
                ],
                max_values=len(prop.get('options', [])) if is_multi else 1,
                min_values=0,
                custom_id=f"select_{prop['name']}")
            select_menu.callback = self.on_select_callback
            self.add_item(select_menu)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Você não pode interagir com o menu de outra pessoa.",
                ephemeral=True)
            return False
        return True

    async def on_select_callback(self, interaction: Interaction):
        prop_name = interaction.data['custom_id'].replace("select_", "")
        values = interaction.data.get('values', [])
        if len(values) > 1:
            self.collected_properties[prop_name] = values
        elif values:
            self.collected_properties[prop_name] = values[0]
        else:
            self.collected_properties.pop(prop_name, None)
        await interaction.response.defer()

    @discord.ui.button(label="✅ Criar Card", style=ButtonStyle.green, row=4)
    async def confirm_button(self, interaction: Interaction, button: Button):
        await interaction.response.defer(ephemeral=True, thinking=True)

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(content="Processando criação do card...", view=self)

        try:
            title_prop_name = next(
                (p['name'] for p in self.all_properties if p['type'] == 'title'), None)
            if not title_prop_name:
                raise NotionAPIError("Nenhuma propriedade de Título foi encontrada na base de dados.")
            
            title_value = self.collected_properties.pop(
                title_prop_name, f"Card criado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")

            # --- INÍCIO DA LÓGICA CORRIGIDA ---

            # Lida com a propriedade de Pessoa Individual (autor do comando)
            if individual_prop := self.config.get('individual_person_prop'):
                # Busca o ID do usuário do Notion com base no nome do Discord
                user_id = self.notion.search_id_person(interaction.user.display_name)
                if user_id:
                    # Armazena o ID encontrado em uma lista
                    self.collected_properties[individual_prop] = [user_id]

            # Lida com a propriedade de Pessoas Coletivas (participantes do tópico)
            if collective_prop := self.config.get('collective_person_prop'):
                if self.thread_context:
                    participants = await get_topic_participants(self.thread_context)
                    notion_user_ids = []
                    for member in participants:
                        # Para cada participante do Discord, busca o ID correspondente no Notion
                        user_id = self.notion.search_id_person(member.display_name)
                        if user_id:
                            notion_user_ids.append(user_id)
                    
                    if notion_user_ids:
                        # Usa um Set para juntar os IDs sem duplicatas, caso a mesma pessoa
                        # seja o autor e participante, e a propriedade seja a mesma.
                        existing_ids = set(self.collected_properties.get(collective_prop, []))
                        new_ids = set(notion_user_ids)
                        self.collected_properties[collective_prop] = list(existing_ids.union(new_ids))
            
            # --- FIM DA LÓGICA CORRIGIDA ---

            if topic_prop_name := self.config.get('topic_link_property_name'):
                if self.thread_context:
                    self.collected_properties[topic_prop_name] = self.thread_context.jump_url

            page_content = await _build_notion_page_content(
                self.config, self.thread_context, self.notion)
            
            # Agora esta função receberá uma lista de IDs para as propriedades de Pessoa
            page_properties = self.notion.build_page_properties(
                self.config['notion_url'], title_value, self.collected_properties)
            
            response = self.notion.insert_into_database(
                self.config['notion_url'], page_properties, children=page_content)
            
            await interaction.edit_original_response(content="✅ Processo concluído.", view=None)

            success_embed = self.notion.format_page_for_embed(
                response, display_properties=self.config.get('display_properties', []))
            
            if not success_embed:
                return await interaction.followup.send("❌ Card criado, mas não foi possível formatar o embed de confirmação.", ephemeral=True)

            success_embed.title = f"✅ Card '{success_embed.title.replace('📌 ', '')}' Criado!"
            success_embed.color = Color.purple()
            publish_view = PublishView(interaction.user.id, success_embed, response['id'], self.config, self.notion)
            
            await interaction.followup.send(
                "Use o botão abaixo para exibir seu card para todos.",
                embed=success_embed,
                view=publish_view,
                ephemeral=True)
                
        except Exception as e:
            await interaction.followup.send(f"🔴 **Erro na criação:**\n`{e}`", ephemeral=True)
            # Re-habilita os botões em caso de erro para que o usuário possa tentar novamente
            for item in self.children:
                item.disabled = False
            await interaction.edit_original_response(content="Ocorreu um erro. Tente novamente.", view=self)

class CardModal(discord.ui.Modal):

    def __init__(self, notion: NotionIntegration, config: dict,
                 all_properties: list, text_props: list, select_props: list,
                 thread_context: Optional[discord.Thread],
                 topic_title: Optional[str]):
        super().__init__(title="Criar Novo Card (Etapa 1/2)")
        self.notion, self.config, self.all_properties, self.text_props, self.select_props, self.thread_context = notion, config, all_properties, text_props, select_props, thread_context
        self.text_inputs = {}
        for prop in self.text_props:
            is_long = any(k in prop['name'].lower() for k in ["desc", "detalhe", "resumo"])
            text_input = discord.ui.TextInput(
                label=prop['name'],
                style=discord.TextStyle.paragraph if is_long else discord.TextStyle.short,
                required=False,
                default=topic_title if prop['type'] == 'title' else None,
                max_length=4000 if is_long else 400
            )
            self.text_inputs[prop['name']] = text_input
            self.add_item(text_input)

    async def on_submit(self, interaction: Interaction):
        collected = {
            name: item.value
            for name, item in self.text_inputs.items() if item.value
        }
        
        if not self.select_props:
             await interaction.response.send_message("❌ Formulário incompleto. Não há propriedades de seleção para continuar.", ephemeral=True)
             return

        await interaction.response.send_message(
            "📝 Etapa 1/2 concluída. Agora, selecione os valores para as propriedades restantes.",
            ephemeral=True)

        view = CardSelectPropertiesView(interaction.user.id, self.config,
                                        self.all_properties,
                                        self.select_props, collected,
                                        self.thread_context, self.notion)
        await interaction.followup.send(view=view, ephemeral=True)


class ContinueEditingView(View):

    def __init__(self, author_id: int):
        super().__init__(timeout=180.0)
        self.author_id, self.choice = author_id, None

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Você não pode interagir com o menu de outra pessoa.",
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✏️ Editar outra propriedade",
                       style=ButtonStyle.secondary)
    async def continue_editing(self, interaction: Interaction, button: Button):
        self.choice = 'continue'
        await interaction.response.edit_message(
            content="Continuando edição...", view=None)
        self.stop()

    @discord.ui.button(label="✅ Concluir Edição", style=ButtonStyle.success)
    async def finish_editing(self, interaction: Interaction, button: Button):
        self.choice = 'finish'
        await interaction.response.edit_message(content="Finalizando...",
                                                view=None)
        self.stop()


class PersonSelectView(View):

    def __init__(self, guild_id: int, channel_id: int, compatible_props: list,
                 config_key: str):
        super().__init__(timeout=180.0)
        select = Select(placeholder="Selecione a propriedade de Pessoa...",
                        options=[
                            SelectOption(label=p['name'],
                                         description=f"Tipo: {p['type']}")
                            for p in compatible_props[:25]
                        ])

        async def callback(interaction: Interaction):
            save_config(guild_id, channel_id,
                        {config_key: interaction.data['values'][0]})
            await interaction.response.edit_message(
                content=f"✅ Configuração salva com sucesso!", view=None)

        select.callback = callback
        self.add_item(select)


class TopicLinkView(View):

    def __init__(self, guild_id: int, channel_id: int, compatible_props: list):
        super().__init__(timeout=180.0)
        select = Select(
            placeholder="Selecione a propriedade para salvar o link...",
            options=[
                SelectOption(label=p['name'], description=f"Tipo: {p['type']}")
                for p in compatible_props[:25]
            ])

        async def callback(interaction: Interaction):
            save_config(
                guild_id, channel_id,
                {'topic_link_property_name': interaction.data['values'][0]})
            await interaction.response.edit_message(
                content=
                f"✅ O link do tópico será salvo na propriedade selecionada.",
                view=None)

        select.callback = callback
        self.add_item(select)


class ManagementView(View):
    """View principal de gerenciamento, agora com o botão de notificações."""
    def __init__(self, parent_interaction: Interaction, notion: NotionIntegration, config: dict):
        super().__init__(timeout=300.0)
        self.parent_interaction = parent_interaction
        self.guild_id = parent_interaction.guild_id
        self.channel_id = parent_interaction.channel.parent_id if isinstance(parent_interaction.channel, discord.Thread) else parent_interaction.channel.id
        self.notion = notion
        self.config = config

    @discord.ui.button(label="Reconfigurar Propriedades", style=ButtonStyle.secondary, emoji="🔄", row=0)
    async def reconfigure(self, interaction: Interaction, button: Button):
        await interaction.response.send_message("Para reconfigurar as propriedades, use `/config` novamente com a URL do Notion.", ephemeral=True)

    @discord.ui.button(label="Gerenciar Botões de Ação", style=ButtonStyle.secondary, emoji="⚙️", row=0)
    async def manage_buttons(self, interaction: Interaction, button: Button):
        is_enabled = self.config.get('action_buttons_enabled', True)
        toggle_view = View(timeout=60.0)
        button_label = "Desativar Botões de Ação" if is_enabled else "Ativar Botões de Ação"
        toggle_button = Button(label=button_label, style=ButtonStyle.danger if is_enabled else ButtonStyle.success)

        async def toggle_callback(inter: Interaction):
            new_state = not is_enabled
            save_config(self.guild_id, self.channel_id, {'action_buttons_enabled': new_state})
            self.config['action_buttons_enabled'] = new_state
            await inter.response.edit_message(content=f"✅ Botões de ação foram {'ATIVADOS' if new_state else 'DESATIVADOS'}.", view=None)

        toggle_button.callback = toggle_callback
        toggle_view.add_item(toggle_button)
        await interaction.response.send_message(f"Os botões de ação (Editar/Excluir) estão **{'ATIVADOS' if is_enabled else 'DESATIVADOS'}**.", view=toggle_view, ephemeral=True)

    @discord.ui.button(label="Resumir com IA", style=ButtonStyle.secondary, emoji="✨", row=1)
    async def manage_ai_summary(self, interaction: Interaction, button: Button):
        is_enabled = self.config.get('ai_summary_enabled', False)
        toggle_view = View(timeout=60.0)
        button_label = "Desativar Resumo por IA" if is_enabled else "Ativar Resumo por IA"
        toggle_button = Button(label=button_label, style=ButtonStyle.danger if is_enabled else ButtonStyle.success)

        async def toggle_callback(inter: Interaction):
            new_state = not is_enabled
            save_config(self.guild_id, self.channel_id, {'ai_summary_enabled': new_state})
            self.config['ai_summary_enabled'] = new_state
            await inter.response.edit_message(content=f"✅ O resumo com IA foi {'ATIVADO' if new_state else 'DESATIVADO'}.", view=None)
            
        toggle_button.callback = toggle_callback
        toggle_view.add_item(toggle_button)
        await interaction.response.send_message(f"O resumo por IA está **{'ATIVADO' if is_enabled else 'DESATIVADO'}**.", view=toggle_view, ephemeral=True)

    @discord.ui.button(label="Configurar Link de Tópico", style=ButtonStyle.secondary, emoji="🔗", row=2)
    async def configure_topic_link(self, interaction: Interaction, button: Button):
        try:
            all_props = self.notion.get_properties_for_interaction(self.config['notion_url'])
            compatible_props = [p for p in all_props if p['type'] in ['rich_text', 'url']]
            if not compatible_props:
                return await interaction.response.send_message("❌ Nenhuma propriedade compatível (Texto ou URL) encontrada.", ephemeral=True)
            view = TopicLinkView(self.guild_id, self.channel_id, compatible_props)
            await interaction.response.send_message("Selecione a propriedade para salvar o link do tópico.", view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"🔴 Erro ao buscar propriedades: {e}", ephemeral=True)

    @discord.ui.button(label="Definir Dono do Card", style=ButtonStyle.secondary, emoji="👤", row=3)
    async def configure_individual_person(self, interaction: Interaction, button: Button):
        try:
            all_props = self.notion.get_properties_for_interaction(self.config['notion_url'])
            people_props = [p for p in all_props if p['type'] == 'people']
            if not people_props:
                return await interaction.response.send_message("❌ Nenhuma propriedade 'Pessoa' encontrada.", ephemeral=True)
            view = PersonSelectView(self.guild_id, self.channel_id, people_props, 'individual_person_prop')
            await interaction.response.send_message("Selecione a propriedade para o autor do comando.", view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"🔴 Erro ao buscar propriedades: {e}", ephemeral=True)

    @discord.ui.button(label="Definir Envolvidos do Tópico", style=ButtonStyle.secondary, emoji="👥", row=3)
    async def configure_collective_person(self, interaction: Interaction, button: Button):
        try:
            all_props = self.notion.get_properties_for_interaction(self.config['notion_url'])
            people_props = [p for p in all_props if p['type'] == 'people']
            if not people_props:
                return await interaction.response.send_message("❌ Nenhuma propriedade 'Pessoa' encontrada.", ephemeral=True)
            view = PersonSelectView(self.guild_id, self.channel_id, people_props, 'collective_person_prop')
            await interaction.response.send_message("Selecione a propriedade para os participantes do tópico.", view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"🔴 Erro ao buscar propriedades: {e}", ephemeral=True)

    @discord.ui.button(label="Configurar Notificações", style=ButtonStyle.primary, emoji="🔔", row=4)
    async def configure_notifications(self, interaction: Interaction, button: Button):
        fresh_config = load_config(self.guild_id, self.channel_id)
        if not fresh_config:
             return await interaction.response.send_message("❌ Erro ao carregar a configuração do canal.", ephemeral=True)

        rules_dashboard_view = NotificationConfigView(self.guild_id,
                                                      self.channel_id,
                                                      fresh_config,
                                                      self.notion, 
                                                      interaction)
        embed = discord.Embed(
            title="⚙️ Gerenciador de Regras de Notificação",
            description="Crie regras para receber notificações automáticas do Notion.",
            color=Color.blue())
        await interaction.response.send_message(embed=embed,
                                                view=rules_dashboard_view,
                                                ephemeral=True)