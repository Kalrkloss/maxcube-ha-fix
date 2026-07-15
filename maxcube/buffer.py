class BufferReader:
    def __init__(self, data: bytes):
        self._data: bytes = data
        self._offset: int = 0

    def __getitem__(self, index: int):
        if index < 0 or index >= len(self._data):
            raise IndexError("index is out of the buffer boundaries")
        return self._data[index]

    def __len__(self) -> int:
        return len(self._data)

    @property
    def offset(self) -> int:
        return self._offset

    @offset.setter
    def offset(self, offset: int):
        if offset < 0 or offset > len(self._data):
            raise IndexError("run out of the buffer")
        self._offset = offset

    @property
    def remaining(self) -> int:
        return len(self._data) - self._offset

    def next_byte(self) -> int:
        offset = self._offset
        self.offset += 1
        return self._data[offset]

    def next_int16_bigendian(self) -> int:
        offset = self._offset
        self.offset += 2
        return self._data[offset] * 256 + self._data[offset +1]
        
    def next_binary_string(self, len: int) -> bytes:
        offset = self._offset
        self.offset += len
        return self._data[offset:self._offset]

    def next_pascal_string(self) -> str:
        return self.next_binary_string(self.next_byte()).decode('utf-8')

    def next_rf_address(self) -> str:
        return self.next_binary_string(3).hex().upper()

    def next_serial(self) -> str:
        return self.next_binary_string(10).decode('ascii')
