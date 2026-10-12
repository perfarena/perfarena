def halves_are_alike(s: str) -> bool:
    def count_vowels(s: str) -> int:
        vowels = 'aeiou'
        return sum(1 for char in s if char.lower() in vowels)

    s = s.lower()
    n = len(s)
    mid = n // 2

    first_half_vowels = count_vowels(s[:mid])
    second_half_vowels = count_vowels(s[mid:])

    return first_half_vowels == second_half_vowels