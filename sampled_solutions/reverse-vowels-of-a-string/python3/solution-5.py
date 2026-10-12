def reverse_vowels(s):
    word = list(s)
    start = 0
    end = len(s) - 1
    vowels = set('aeiouAEIOU')

    while start < end:
        while start < end and word[start] not in vowels:
            start += 1
        while start < end and word[end] not in vowels:
            end -= 1

        if word[start] in vowels and word[end] in vowels:
            word[start], word[end] = word[end], word[start]
            start += 1
            end -= 1

    return ''.join(word)

# Test the function
print(reverse_vowels("hello"))  # Output: "holle"
print(reverse_vowels("Elite code"))  # Output: "olota cede"