def length_of_longest_substring(s):
    longest = 0
    current_sub = 0
    current_sub_start = 0
    sub = {}

    for i, letter in enumerate(s):
        if letter in sub and sub[letter] >= current_sub_start:
            current_sub_start = sub[letter] + 1
            current_sub = i - current_sub_start + 1
        else:
            current_sub += 1

        sub[letter] = i
        longest = max(longest, current_sub)

    return longest