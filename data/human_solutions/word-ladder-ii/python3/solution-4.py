result = []
temp = []

if end_word not in word_set:
    return []

def dfs(word):
    temp.append(word)
    if word == end_word:
        result.append(list(temp))
    for neighbor in graph[word]:
        if distance[neighbor] == distance[word] - 1:
            dfs(neighbor)
    temp.pop()