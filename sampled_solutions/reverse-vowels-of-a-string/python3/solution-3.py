def reverse_vowels(input_str):
    vowels = "aeiouAEIOU"
    str_list = list(input_str)
    i, j = 0, len(str_list) - 1

    while i < j:
        if str_list[i] not in vowels:
            i += 1
        elif str_list[j] not in vowels:
            j -= 1
        else:
            str_list[i], str_list[j] = str_list[j], str_list[i]
            i += 1
            j -= 1

    return "".join(str_list)

# Test the function
input_str = "from the given string we need to reverse the positions of vowels"
result = reverse_vowels(input_str)
print(result)