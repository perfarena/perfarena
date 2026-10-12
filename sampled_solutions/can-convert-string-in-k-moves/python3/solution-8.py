def minDeletions(s: str) -> int:
    counter = [0] * 26
    for char in s:
        counter[ord(char) - ord('a')] += 1

    counter_set = set()
    deletions = 0
    for count in counter:
        while count in counter_set:
            count -= 1
            deletions += 1
        if count > 0:
            counter_set.add(count)

    return deletions

# Example usage
input_string = "aaabbbcccddddee"
result = minDeletions(input_string)
print(result)