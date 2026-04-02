import random

def generate_prompts(num_questions: int) -> list[str]:
    templates = [
        "Explain your reasoning clearly.",
        "Show all intermediate steps and calculation details.",
        "State assumptions and justify your chosen approach.",
        "Compare two valid solutions and discuss their trade-offs.",
        "Provide a concise argument followed by your final answer.",
        "Draw a diagram or visual representation if it helps clarify your explanation.",
        "Analyze the time complexity (Big O) and space complexity of your algorithm.",
        "Explain how you would handle potential edge cases or invalid inputs.",
        "Discuss the physical principles or mathematical proofs underlying your result.",
        "If this were implemented in a real-world system, what security or performance concerns would you prioritize?",
        "Define the core components of the system and how they interact.",
        "Explain the historical context or theoretical motivation for this concept.",
        "Critically evaluate the limitations and potential biases of the proposed model.",
        "Synthesize concepts from two different fields to solve the problem.",
        "Write a pseudocode refinement of your solution.",
        "Contrast these findings with current industry best practices.",
        "Describe the ethical implications of this approach in a global context.",
        "Design a test suite that would validate the robustness of this solution.",
        "Identify the primary bottle-necks in this architecture and suggest improvements.",
        "Explain the biological mechanism behind this phenomenon and its evolutionary significance.",
        "Analyze the chemical reaction pathways and energy transformations involved.",
        "Interpret the literary themes present and how they reflect the author's intent.",
        "Propose a user experience (UX) improvement that aligns with modern design principles.",
        "Translate this high-level problem into a low-level implementation strategy.",
    ]

    return [templates[i % len(templates)] for i in range(num_questions)]
