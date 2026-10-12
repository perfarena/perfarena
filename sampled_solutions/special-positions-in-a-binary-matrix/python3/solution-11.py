def halves_are_alike(s: str) -> bool:
    def count_vowels(s: str) -> int:
        vowels = "aeiouAEIOU"
        return sum(1 for char in s if char in vowels)

    half_length = len(s) // 2
    a = s[:half_length]
    b = s[half_length:]

    count_a = count_vowels(a)
    count_b = count_vowels(b)

    return count_a == count_b

# Test the function
s = "book"
print(halves_are_alike(s))  # Output: True