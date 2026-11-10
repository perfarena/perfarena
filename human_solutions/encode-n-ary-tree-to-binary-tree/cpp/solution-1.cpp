#include <unordered_set>
#include <string>

int numJewelsInStones(std::string jewels, std::string stones) {
    std::unordered_set<char> jewelSet;
    for (char j : jewels) {
        jewelSet.insert(j);
    }

    int count = 0;
    for (char s : stones) {
        if (jewelSet.find(s) != jewelSet.end()) {
            count++;
        }
    }

    return count;
}