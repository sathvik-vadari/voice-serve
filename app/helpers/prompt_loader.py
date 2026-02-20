"""Helper for loading prompts from the prompts folder."""
import os
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class PromptLoader:
    """Handles loading and managing prompts from the prompts folder."""
    
    def __init__(self, prompts_dir: str = "app/prompts"):
        self.prompts_dir = Path(prompts_dir)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
    
    def load_prompt(self, prompt_name: str) -> Optional[str]:
        """
        Load a prompt from a file in the prompts directory.
        
        Args:
            prompt_name: Name of the prompt file (without extension)
            
        Returns:
            Prompt content as string, or None if file doesn't exist
        """
        prompt_file = self.prompts_dir / f"{prompt_name}.txt"
        
        if not prompt_file.exists():
            logger.warning(f"Prompt file not found: {prompt_file}")
            return None
        
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            logger.info(f"Loaded prompt from {prompt_file}")
            return content
        except Exception as e:
            logger.error(f"Error loading prompt {prompt_name}: {e}")
            return None
    
    def save_prompt(self, prompt_name: str, content: str) -> bool:
        """
        Save a prompt to a file in the prompts directory.
        
        Args:
            prompt_name: Name of the prompt file (without extension)
            content: Content to save
            
        Returns:
            True if successful, False otherwise
        """
        prompt_file = self.prompts_dir / f"{prompt_name}.txt"
        
        try:
            with open(prompt_file, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Saved prompt to {prompt_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving prompt {prompt_name}: {e}")
            return False
    
    def list_prompts(self) -> list[str]:
        """List all available prompt files."""
        prompts = []
        if self.prompts_dir.exists():
            for file in self.prompts_dir.glob("*.txt"):
                prompts.append(file.stem)
        return sorted(prompts)
    
    def get_default_prompt(self) -> str:
        """Get the default prompt if it exists, otherwise return a default message."""
        default = self.load_prompt("default")
        if default:
            return default
        
        return (
            "You are a helpful AI assistant. Respond naturally and conversationally. "
            "Keep your responses concise but engaging."
        )

