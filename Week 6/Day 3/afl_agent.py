"""
AFL ANALYTICS AGENT
===================

Gemini 3.5 Flash-Lite powered AFL analytics agent.

The Google GenAI SDK is used directly so Gemini function-call
thought signatures are preserved correctly.

The local AFLDataStore remains the source of truth for AFL data.
"""

from __future__ import annotations

import os
from typing import Any

from google import genai
from google.genai import types

from afl_data import STORE


# ================================================================
# SYSTEM PROMPT
# ================================================================

SYSTEM_PROMPT = """
You are an expert AFL analytics assistant.

Use the supplied AFL data tools whenever the user asks about:

- player statistics
- player rankings
- team statistics
- matches
- head-to-head records
- AFL seasons
- numerical comparisons

Rules:

1. Never invent AFL statistics.
2. Use the data tools for factual AFL statistics.
3. Preserve the ordering returned by leaderboard tools.
4. Mention the relevant season.
5. Use exact names returned by the data layer.
6. If information is unavailable, say so.
7. Keep answers concise and useful.
"""


class AFLChatAgent:
    """
    Gemini-powered AFL analytics agent.

    Public interface:

        agent.chat(question)
        agent.reset()

    Public attributes:

        history
        model_name
        llm
        tools
        tool_map
    """

    # Gemini model used by the agent.
    MODEL_NAME = "gemini-3.5-flash-lite"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ):
        """Initialize the Gemini client and local AFL tools."""

        self.model_name = (
            model_name or self.MODEL_NAME
        )

        # Read the API key from the argument or environment.
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
        )

        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured. "
                "Set it before creating AFLChatAgent."
            )

        # Google GenAI client.
        self.llm = genai.Client(
            api_key=self.api_key
        )

        # IMPORTANT:
        # Keep complete Gemini Content objects here.
        #
        # Gemini 3.x function calls can contain thought_signature
        # metadata. Reconstructing messages manually can remove that
        # metadata and cause a 400 function-call error.
        self.history: list[Any] = []

        # Local AFL tools.
        self.tool_map = {
            "player_leaders": self._tool_player_leaders,
            "player_summary": self._tool_player_summary,
            "team_summary": self._tool_team_summary,
            "head_to_head": self._tool_head_to_head,
            "match_lookup": self._tool_match_lookup,
        }

        self.tools = list(
            self.tool_map.keys()
        )

        self._tool_declarations = (
            self._build_tool_declarations()
        )

    # ============================================================
    # GEMINI TOOL DECLARATIONS
    # ============================================================

    def _build_tool_declarations(self):
        """Create Gemini function declarations."""

        return [
            types.FunctionDeclaration(
                name="player_leaders",
                description=(
                    "Return the top AFL players for a statistic "
                    "in a specified season."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "metric": types.Schema(
                            type="STRING",
                            description=(
                                "Statistic such as disposals, goals, "
                                "kicks, marks, tackles, handballs, "
                                "fantasy points, or clearances."
                            ),
                        ),
                        "season": types.Schema(
                            type="INTEGER",
                            description="AFL season year.",
                        ),
                        "limit": types.Schema(
                            type="INTEGER",
                            description="Number of players.",
                        ),
                    },
                    required=[
                        "metric",
                        "season",
                        "limit",
                    ],
                ),
            ),

            types.FunctionDeclaration(
                name="player_summary",
                description=(
                    "Return an AFL player statistical summary."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "player": types.Schema(
                            type="STRING",
                            description="AFL player name.",
                        ),
                        "season": types.Schema(
                            type="INTEGER",
                            description="AFL season.",
                        ),
                    },
                    required=[
                        "player",
                        "season",
                    ],
                ),
            ),

            types.FunctionDeclaration(
                name="team_summary",
                description=(
                    "Return an AFL team statistical summary."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "team": types.Schema(
                            type="STRING",
                            description="AFL team name.",
                        ),
                        "season": types.Schema(
                            type="INTEGER",
                            description="AFL season.",
                        ),
                    },
                    required=[
                        "team",
                        "season",
                    ],
                ),
            ),

            types.FunctionDeclaration(
                name="head_to_head",
                description=(
                    "Return head-to-head AFL results between "
                    "two teams over a season range."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "team_a": types.Schema(
                            type="STRING"
                        ),
                        "team_b": types.Schema(
                            type="STRING"
                        ),
                        "start_season": types.Schema(
                            type="INTEGER"
                        ),
                        "end_season": types.Schema(
                            type="INTEGER"
                        ),
                    },
                    required=[
                        "team_a",
                        "team_b",
                        "start_season",
                        "end_season",
                    ],
                ),
            ),

            types.FunctionDeclaration(
                name="match_lookup",
                description=(
                    "Find AFL matches involving a team "
                    "in a season."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "team_a": types.Schema(
                            type="STRING"
                        ),
                        "season": types.Schema(
                            type="INTEGER"
                        ),
                        "team_b": types.Schema(
                            type="STRING"
                        ),
                    },
                    required=[
                        "team_a",
                        "season",
                    ],
                ),
            ),
        ]

    # ============================================================
    # LOCAL AFL TOOLS
    # ============================================================

    def _tool_player_leaders(
        self,
        metric: str,
        season: int,
        limit: int = 5,
    ) -> str:
        """Return player leaderboard information."""

        return STORE.player_leaders(
            metric,
            int(season),
            int(limit),
        )

    def _tool_player_summary(
        self,
        player: str,
        season: int,
    ) -> str:
        """Return player summary information."""

        return STORE.player_summary(
            player,
            int(season),
        )

    def _tool_team_summary(
        self,
        team: str,
        season: int,
    ) -> str:
        """Return team summary information."""

        return STORE.team_summary(
            team,
            int(season),
        )

    def _tool_head_to_head(
        self,
        team_a: str,
        team_b: str,
        start_season: int,
        end_season: int,
    ) -> str:
        """Return head-to-head information."""

        return STORE.head_to_head(
            team_a,
            team_b,
            int(start_season),
            int(end_season),
        )

    def _tool_match_lookup(
        self,
        team_a: str,
        season: int,
        team_b: str | None = None,
    ) -> str:
        """Return match lookup information."""

        return STORE.match_lookup(
            team_a,
            int(season),
            team_b=team_b,
        )

    # ============================================================
    # TOOL DISPATCH
    # ============================================================

    def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Execute a local AFL tool safely."""

        tool = self.tool_map.get(name)

        if tool is None:
            return f"Unknown AFL tool: {name}"

        try:
            return str(
                tool(**arguments)
            )
        except Exception as exc:
            return (
                f"Tool '{name}' failed: "
                f"{type(exc).__name__}: {exc}"
            )

    # ============================================================
    # CHAT
    # ============================================================

    def chat(
        self,
        question: str,
        max_tool_rounds: int = 5,
    ) -> str:
        """
        Answer a question using Gemini and AFL tools.

        The original Gemini response Content object is preserved
        in history. This keeps function-call thought signatures
        intact for subsequent tool responses.
        """

        if not question or not question.strip():
            return "Please provide a question."

        # Add user message.
        self.history.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=question.strip()
                    )
                ],
            )
        )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[
                types.Tool(
                    function_declarations=(
                        self._tool_declarations
                    )
                )
            ],
        )

        # --------------------------------------------------------
        # Gemini / tool execution loop.
        # --------------------------------------------------------

        for _ in range(max_tool_rounds):

            response = (
                self.llm.models.generate_content(
                    model=self.model_name,
                    contents=self.history,
                    config=config,
                )
            )

            if not response.candidates:
                return "Gemini returned no response."

            candidate = response.candidates[0]

            if candidate.content is None:
                return "Gemini returned an empty response."

            # CRITICAL:
            #
            # Store the ORIGINAL Gemini Content object.
            #
            # Do not rebuild function-call messages manually.
            # Gemini 3.x can attach thought_signature to these
            # objects.
            model_content = candidate.content

            self.history.append(
                model_content
            )

            function_calls = [
                part.function_call
                for part in model_content.parts
                if part.function_call is not None
            ]

            # No function call means this is the final answer.
            if not function_calls:

                text = "".join(
                    part.text or ""
                    for part in model_content.parts
                    if part.text
                ).strip()

                return (
                    text
                    if text
                    else "Gemini returned no textual answer."
                )

            # Execute each requested tool.
            function_response_parts = []

            for call in function_calls:

                name = call.name

                arguments = dict(
                    call.args or {}
                )

                result = self._execute_tool(
                    name,
                    arguments,
                )

                function_response_parts.append(
                    types.Part.from_function_response(
                        name=name,
                        response={
                            "result": result
                        },
                    )
                )

            # Return tool results to Gemini.
            self.history.append(
                types.Content(
                    role="user",
                    parts=function_response_parts,
                )
            )

        return (
            "The agent reached its maximum tool-call limit "
            "without producing a final answer."
        )

    # ============================================================
    # RESET
    # ============================================================

    def reset(self):
        """Clear conversation history."""

        self.history = []


# Backwards-compatible alias.
GeminiAFLAgent = AFLChatAgent
