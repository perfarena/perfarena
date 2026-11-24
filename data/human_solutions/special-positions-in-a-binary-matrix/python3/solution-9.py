class Solution:
    def halvesAreAlike(self, s: str) -> bool:
        def count_vowels(s: str) -> int:
            vowels = "aeiou"
            count = 0
            for c in s:
                if c.lower() in vowels:
                    count += 1
            return count
        
        length = len(s)
        part1 = s[:length // 2]
        part2 = s[length // 2:]
        
        return count_vowels(part1) == count_vowels(part2)