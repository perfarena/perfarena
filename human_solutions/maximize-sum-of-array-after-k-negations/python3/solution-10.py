def removeAdjacentDuplicates(inputString):
    answer = ""
    for char in inputString:
        if answer and char == answer[-1]:
            answer = answer[:-1]
        else:
            answer += char
    return answer

# Example usage
inputString = "ka"
result = removeAdjacentDuplicates(inputString)
print(result)  # Output: "ka"