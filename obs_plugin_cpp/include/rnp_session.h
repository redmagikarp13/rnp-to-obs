#pragma once

#include "obs_headers.h"
#include <string>
#include <vector>
#include <map>
#include <memory>
#include <mutex>
#include <functional>

struct RNPParticipant {
    std::string userId;
    std::string name;
    std::string role;
    bool isTalking;
    bool isMuted;
    bool hasVideo;
    std::string videoStreamId;
};

struct RNPChatMessage {
    std::string messageId;
    std::string senderId;
    std::string senderName;
    std::string text;
    std::string timestamp;
};

class RNPSession {
public:
    static RNPSession& getInstance();

    bool connectToRoom(const std::string& roomUrl, const std::string& userName, const std::string& email);
    void disconnect();
    bool isConnected() const;

    // Metadados da Reunião
    std::string getRoomTitle() const;
    std::vector<RNPParticipant> getParticipants();
    std::vector<RNPChatMessage> getRecentChatMessages();

    // Callbacks para os Sources do OBS
    void registerCameraCallback(const std::string& sourceId, std::function<void(const struct obs_source_frame*)> callback);
    void unregisterCameraCallback(const std::string& sourceId);

    void registerSlideCallback(const std::string& sourceId, std::function<void(const struct obs_source_frame*)> callback);
    void unregisterSlideCallback(const std::string& sourceId);

private:
    RNPSession();
    ~RNPSession();

    bool m_connected;
    std::string m_roomUrl;
    std::string m_userName;
    std::string m_email;
    std::string m_sessionToken;

    mutable std::mutex m_sessionMutex;
    std::vector<RNPParticipant> m_participants;
    std::vector<RNPChatMessage> m_chatMessages;

    std::map<std::string, std::function<void(const struct obs_source_frame*)>> m_cameraCallbacks;
    std::map<std::string, std::function<void(const struct obs_source_frame*)>> m_slideCallbacks;
};
