from litellm import (
    ChatCompletionToolParam,
    ChatCompletionToolParamFunctionChunk,
)


_FINISH_DESCRIPTION = """Finish the interaction when the task is complete OR if the assistant cannot proceed further with the task. Put the final candidate locations in the required locations string, one file:QualifiedName per line, ordered by relevance."""

FinishTool = ChatCompletionToolParam(
    type='function',
        function=ChatCompletionToolParamFunctionChunk(
            name='finish',
            description=_FINISH_DESCRIPTION,
            parameters={
                'type': 'object',
                'properties': {
                    'locations': {
                        'type': 'string',
                        'description': 'Final candidate locations, one file:QualifiedName per line, ordered by relevance.',
                    },
                },
                'required': ['locations'],
                'additionalProperties': False,
            },
        ),
)
