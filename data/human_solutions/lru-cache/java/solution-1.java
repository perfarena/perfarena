// Define the private member variables in the class
int capacity;
LinkedHashMap<Integer, Integer> map;

// Implement the constructor
LRUCache(int capacity) {
    this.capacity = capacity;
    map = new LinkedHashMap<>(capacity, 0.75f, true);
}

// Implement the get method
public int get(int key) {
    return map.getOrDefault(key, -1);
}

// Implement the put method
public void put(int key, int value) {
    map.put(key, value);
    if (map.size() > capacity) {
        map.remove(map.keySet().iterator().next());
    }
}