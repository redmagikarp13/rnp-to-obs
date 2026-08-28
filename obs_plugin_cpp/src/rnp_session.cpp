#include "rnp_session.h"
#include "rnp_sources.h"
#include <stdio.h>

extern "C" {
void register_rnp_sources(void) {
    obs_register_source(&rnp_camera_source_info);
    obs_register_source(&rnp_presentation_source_info);
    obs_register_source(&rnp_chat_source_info);
}
}

RNPSession::RNPSession() : m_connected(false) {}

RNPSession::~RNPSession() {
    disconnect();
}

RNPSession& RNPSession::getInstance() {
    static RNPSession instance;
    return instance;
}

bool RNPSession::connectToRoom(const std::string& roomUrl, const std::string& userName, const std::string& email) {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    m_roomUrl = roomUrl;
    m_userName = userName;
    m_email = email;
    m_connected = true;

    blog(LOG_INFO, "[RNPSession] Conectando à sala RNP: %s (Usuário: %s)", roomUrl.c_str(), userName.c_str());
    return true;
}

void RNPSession::disconnect() {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    if (m_connected) {
        blog(LOG_INFO, "[RNPSession] Desconectando da sala RNP.");
        m_connected = false;
        m_participants.clear();
        m_chatMessages.clear();
    }
}

bool RNPSession::isConnected() const {
    return m_connected;
}

std::string RNPSession::getRoomTitle() const {
    return "Webconferência RNP";
}

std::vector<RNPParticipant> RNPSession::getParticipants() {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    return m_participants;
}

std::vector<RNPChatMessage> RNPSession::getRecentChatMessages() {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    return m_chatMessages;
}

void RNPSession::registerCameraCallback(const std::string& sourceId, std::function<void(const struct obs_source_frame*)> callback) {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    m_cameraCallbacks[sourceId] = callback;
}

void RNPSession::unregisterCameraCallback(const std::string& sourceId) {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    m_cameraCallbacks.erase(sourceId);
}

void RNPSession::registerSlideCallback(const std::string& sourceId, std::function<void(const struct obs_source_frame*)> callback) {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    m_slideCallbacks[sourceId] = callback;
}

void RNPSession::unregisterSlideCallback(const std::string& sourceId) {
    std::lock_guard<std::mutex> lock(m_sessionMutex);
    m_slideCallbacks.erase(sourceId);
}
