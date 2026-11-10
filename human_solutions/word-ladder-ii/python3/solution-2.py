from collections import defaultdict

graph = defaultdict(set)
word_set = set(word_list + [begin_word])

for word in word_set:
    for i in range(len(word)):
        for j in range(26):
            temp = word[:i] + chr(97 + j) + word[i + 1:]
            if temp in word_set and temp != word:
                graph[word].add(temp)