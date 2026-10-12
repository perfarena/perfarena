def word_ladder(beginWord, endWord, wordList):
    wordSet = set(wordList)
    if endWord not in wordSet:
        return []

    queue = collections.deque([(beginWord, [beginWord])])
    visited = set()
    distance = {beginWord: 0}
    result = []

    while queue:
        word, path = queue.popleft()
        if word == endWord:
            result.append(path)
            continue

        for i in range(len(word)):
            for c in 'abcdefghijklmnopqrstuvwxyz':
                newWord = word[:i] + c + word[i + 1:]
                if newWord in wordSet and newWord not in visited:
                    visited.add(newWord)
                    queue.append((newWord, path + [newWord]))
                    distance[newWord] = distance[word] + 1

    return result

# Example Usage
beginWord = "hit"
endWord = "cog"
wordList = ["hot", "dot", "dog", "lot", "log", "cog"]
result = word_ladder(beginWord, endWord, wordList)
print(result)