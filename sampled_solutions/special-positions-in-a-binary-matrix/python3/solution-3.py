def halvesAreAlike(s: str) -> bool:
    def count_vowels(s: str) -> int:
        vowels = "aeiouAEIOU"
        return sum(1 for char in s if char in vowels)

    length = len(s)
    half_length = length // 2
    first_half = s[:half_length]
    second_half = s[half_length:]

    return count_vowels(first_half) == count_vowels(second_half)