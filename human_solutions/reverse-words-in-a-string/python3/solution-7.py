# Trim any extra spaces in the string
trimmed_string = ' '.join(input_string.split())

# Reverse the words in the string
reversed_words = trimmed_string.split()[::-1]

# Return the reversed string
reversed_string = ' '.join(reversed_words)
return reversed_string