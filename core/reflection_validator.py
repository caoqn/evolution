"""
Validation module for evolution patches.

Patches generated during L1/L2/L3 reflection must pass quality gates
before being persisted to the team. This prevents bad patches from
accumulating and degrading performance.
"""

import re
from typing import Tuple
from pathlib import Path


class PatchValidator:
    """Validates patches before they're applied and persisted."""
    
    # Patterns for generic/useless patches
    GENERIC_PATTERNS = [
        r"always verify",
        r"always check",
        r"remember to",
        r"be more careful",
        r"pay attention",
        r"double-check",
        r"make sure to",
        r"should probably",
        r"try to be more",
    ]
    
    # Patterns for too-narrow patches (file-specific fixes)
    NARROW_PATTERNS = [
        r"fix line \d+",
        r"in file .*\.py",
        r"in the function.*:",
        r"in class .*:",
        r"change.*\.py:line",
    ]
    
    # Min/max reasonable patch sizes
    MIN_PATCH_CHARS = 10
    MAX_PATCH_CHARS = 2000
    
    @classmethod
    def validate_patch(cls, patch: str, agent_name: str = None) -> Tuple[bool, str]:
        """
        Validate a patch before applying it.
        
        Args:
            patch: The patch text
            agent_name: Optional agent name for context
            
        Returns:
            (is_valid, reason) tuple. If is_valid=False, reason explains why.
        """
        if not patch or not isinstance(patch, str):
            return False, "Patch must be non-empty string"
        
        patch = patch.strip()
        
        # Check: Empty after stripping
        if not patch:
            return False, "Patch is empty after stripping"
        
        # Check: Size bounds
        if len(patch) < cls.MIN_PATCH_CHARS:
            return False, f"Patch too short (<{cls.MIN_PATCH_CHARS} chars): '{patch}'"
        
        if len(patch) > cls.MAX_PATCH_CHARS:
            return False, f"Patch too long (>{cls.MAX_PATCH_CHARS} chars): '{patch[:100]}...'"
        
        # Check: Generic patterns (low-value advice)
        patch_lower = patch.lower()
        for pattern in cls.GENERIC_PATTERNS:
            if re.search(pattern, patch_lower):
                return False, f"Patch too generic (matches '{pattern}'): '{patch[:80]}...'"
        
        # Check: Too narrow (file-specific fixes)
        for pattern in cls.NARROW_PATTERNS:
            if re.search(pattern, patch, re.IGNORECASE):
                return False, f"Patch too narrow/file-specific (matches '{pattern}'): '{patch[:80]}...'"
        
        # Check: Instruction-like (imperatives at start, often less useful)
        # Allow some imperatives but not exclusively
        imperative_words = ["replace", "delete", "add", "remove", "fix", "change"]
        starts_with_imperative = any(patch.lower().startswith(w) for w in imperative_words)
        
        if starts_with_imperative and len(patch.split()) <= 3:
            return False, f"Patch too terse/command-like: '{patch}'"
        
        return True, ""
    
    @classmethod
    def validate_skill(cls, skill_code: str, skill_name: str = None) -> Tuple[bool, str]:
        """
        Validate a skill/tool before applying it.
        
        Args:
            skill_code: The skill code
            skill_name: Optional skill name for context
            
        Returns:
            (is_valid, reason) tuple.
        """
        if not skill_code or not isinstance(skill_code, str):
            return False, "Skill must be non-empty string"
        
        skill_code = skill_code.strip()
        
        if not skill_code:
            return False, "Skill is empty after stripping"
        
        # Check: Must look like Python code (very basic check)
        if not any(kw in skill_code for kw in ["def ", "class ", "import ", "return ", "async "]):
            return False, "Skill doesn't look like Python code"
        
        # Check: Must have some content
        lines = [l.strip() for l in skill_code.split('\n') if l.strip() and not l.strip().startswith('#')]
        if len(lines) < 2:
            return False, "Skill too minimal (need at least 2 non-comment lines)"
        
        return True, ""
    
    @classmethod
    def check_contradiction(
        cls, 
        new_patch: str, 
        existing_patches: list[str]
    ) -> Tuple[bool, str]:
        """
        Check if a new patch contradicts existing patches.
        
        Args:
            new_patch: The new patch being proposed
            existing_patches: List of existing patches already in the system
            
        Returns:
            (has_contradiction, explanation) tuple.
        """
        if not existing_patches:
            return False, ""
        
        new_lower = new_patch.lower()
        
        # Look for direct contradictions (opposite advice)
        contradiction_pairs = [
            ("run tests", "skip tests"),
            ("run all tests", "run only relevant tests"),
            ("check immediately", "check later"),
            ("prioritize speed", "prioritize accuracy"),
            ("fail fast", "try all options"),
            ("ask for help", "work independently"),
            ("use caching", "don't cache"),
        ]
        
        for existing in existing_patches:
            existing_lower = existing.lower()
            
            for term1, term2 in contradiction_pairs:
                has_term1_new = term1 in new_lower
                has_term2_new = term2 in new_lower
                has_term1_existing = term1 in existing_lower
                has_term2_existing = term2 in existing_lower
                
                if (has_term1_new and has_term2_existing) or (has_term2_new and has_term1_existing):
                    return True, f"Contradicts existing patch: new='{new_patch[:50]}...' vs existing='{existing[:50]}...'"
        
        return False, ""


class PromptPatchValidator(PatchValidator):
    """Specialized validator for prompt patches."""
    
    @classmethod
    def validate_for_prompt(cls, patch: str, agent_name: str = None) -> Tuple[bool, str]:
        """
        Validate a patch intended for inclusion in agent prompts.
        
        Args:
            patch: The patch text
            agent_name: Optional agent name
            
        Returns:
            (is_valid, reason) tuple.
        """
        is_valid, reason = cls.validate_patch(patch, agent_name)
        if not is_valid:
            return False, reason
        
        # Additional checks for prompts
        
        # Should be written as advice/guidance, not imperatives
        if patch.strip().endswith("!"):
            return False, "Patch reads as command (ends with '!'), not guidance"
        
        # Check length is reasonable for inclusion in a prompt
        if len(patch) > 500:
            # Long patches are OK but should be well-structured
            if not any(c in patch for c in ["-", "*", "\n", "1.", "2."]):
                return False, "Long patch should have structure (bullets, numbered list, etc.)"
        
        return True, ""
